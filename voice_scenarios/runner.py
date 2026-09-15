import asyncio
import json
import shutil
import time
import wave
from dataclasses import asdict
from pathlib import Path

from .clock_timeline import client_wall_time
from .diagnostics import DiagnosticCollector
from .player import ClockedPlayer
from .protocol import Event


def save_result(result, output_dir):
    temporary = output_dir / "result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temporary.replace(output_dir / "result.json")


async def run_scenario(
    scenario, transport, output_dir, *, player_factory=ClockedPlayer
):
    """Run all turns on one connection; preserve partial results on failure."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # Capture once, before connecting. No server offset estimation and no extra
    # wall-clock syscall per audio frame; playback timing stays monotonic.
    client_clock = {
        "monotonic_ns": time.monotonic_ns(),
        "wall_time_ns": time.time_ns(),
    }
    result = {
        "schema_version": 1,
        "name": scenario.name,
        "status": "running",
        "turns": [],
        "client_clock": client_clock,
        "connection_started_at_ns": client_clock["monotonic_ns"],
    }
    if scenario.evaluation:
        result["evaluation"] = scenario.evaluation
        result["evaluation_turns"] = [
            {"id": turn.id, "tool": turn.tool, "input_text": turn.input_text}
            for turn in scenario.turns
        ]
    events = asyncio.Queue()
    collector = None
    journal = (output_dir / "client-events.jsonl").open("w")

    def emit(event):
        if collector:
            if event.kind == "diagnostics_started":
                collector.bind_session(event.data["server_session_id"])
            elif event.kind == "hello_received":
                collector.bind_session(event.data["session_id"])
            elif event.kind == "diagnostics":
                collector.accept(event.data)
                return
        record = {
            "schema_version": 1,
            "source": "python",
            "clock_id": "python-process",
            "event": event.kind,
            "monotonic_ns": event.at_ns,
            "wall_time_ns": client_wall_time(event.at_ns, client_clock),
            "data": {k: v for k, v in event.data.items() if k != "pcm"},
        }
        if event.kind != "vas_event":
            journal.write(json.dumps(record, ensure_ascii=False) + "\n")
            journal.flush()
        events.put_nowait(event)

    try:
        if getattr(transport, "diagnostics", "off") != "off":
            collector = DiagnosticCollector(output_dir, emit)
        result["session"] = await transport.connect(emit)
        result["startup"] = {}
        await _wait_for_greeting(
            scenario,
            events,
            output_dir,
            player_factory,
            emit,
            result["startup"],
            client_clock,
        )
        for index, turn in enumerate(scenario.turns, 1):
            item = await _run_turn(
                scenario,
                turn,
                transport,
                events,
                output_dir,
                index,
                player_factory,
                emit,
                client_clock=client_clock,
            )
            result["turns"].append(item)
            save_result(result, output_dir)
            if item["status"] == "failed":
                break
        result["status"] = (
            "passed"
            if len(result["turns"]) == len(scenario.turns)
            and all(t["status"] != "failed" for t in result["turns"])
            else "failed"
        )
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            await transport.close()
        except Exception as exc:
            result.update(status="failed", close_error=type(exc).__name__)
        if collector:
            result["diagnostics"] = await collector.finish()
            if not collector.complete:
                result["status"] = "failed"
        journal.close()
        save_result(result, output_dir)
    return result


async def _wait_for_greeting(
    scenario, events, directory, player_factory, sink, result, client_clock
):
    """Consume connection output before any listen/start or microphone frames."""
    started = time.monotonic_ns()
    result.update(
        status="waiting",
        started_at_ns=started,
        events=[],
        audio={
            "received": str(directory / "greeting.received.wav"),
            "played": str(directory / "greeting.played.wav"),
        },
    )
    player = player_factory(sink, directory / "greeting.played.wav")
    writer = None
    frames = 0
    observed = stopped = drained = False
    deadline = time.monotonic() + scenario.greeting_timeout_seconds
    quiet = time.monotonic() + scenario.greeting_wait_seconds
    try:
        while True:
            wake = min(deadline, quiet) if quiet is not None else deadline
            try:
                event = await asyncio.wait_for(
                    events.get(), max(0, wake - time.monotonic())
                )
            except asyncio.TimeoutError:
                if (
                    quiet is not None
                    and time.monotonic() >= quiet
                    and (not observed or stopped and drained)
                ):
                    result["status"] = "completed" if observed else "not_observed"
                    break
                raise TimeoutError("greeting_timeout: first input was not sent")
            data = {k: v for k, v in event.data.items() if k != "pcm"}
            record = {
                "event": event.kind,
                "at_ns": event.at_ns,
                "wall_time_ns": client_wall_time(event.at_ns, client_clock),
                "data": data,
            }
            result["events"].append(record)
            if (
                event.kind == "vas_event"
                and data.get("listen_turn_id") is None
                and data.get("event")
                in {"tts_first_text", "tts_text_enqueued", "tts_request_started"}
            ):
                observed = True
                quiet = None
            elif event.kind in {"tts_start", "tts_sentence_start", "pcm"}:
                observed = True
                quiet = None
                if event.kind == "pcm":
                    pcm, rate = event.data["pcm"], event.data["sample_rate"]
                    if writer is None:
                        writer = wave.open(result["audio"]["received"], "wb")
                        writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                    writer.writeframes(pcm)
                    frames += 1
                    data.update(
                        bytes=len(pcm), output_kind="greeting", is_session_output=True
                    )
                    data["duration_ms"] = len(pcm) * 1000 / (rate * 2)
                    record["event"] = "audio_received"
                    player.feed(
                        pcm,
                        rate,
                        {"audio_seq": data.get("audio_seq"), "is_session_output": True},
                    )
            elif event.kind == "tts_stop":
                observed = stopped = True
                result["server_stop_at_ns"] = event.at_ns
                player.finish()
                if not frames:
                    drained = True
                    result["playback_drained_at_ns"] = event.at_ns
            elif event.kind == "playback_drained":
                drained = True
                result["playback_drained_at_ns"] = event.at_ns
            elif event.kind in {"error", "disconnected"}:
                raise RuntimeError(event.data.get("message", event.kind))
            if stopped and drained:
                quiet = time.monotonic() + scenario.settle_seconds
        result["observed"] = observed
        sink(
            Event("greeting_ready", {"observed": observed, "status": result["status"]})
        )
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        await player.close()
        if writer is not None:
            writer.close()
        result.update(ended_at_ns=time.monotonic_ns(), received_frames=frames)


async def _run_turn(
    scenario,
    turn,
    transport,
    events,
    output_dir,
    index,
    player_factory,
    sink=None,
    *,
    client_clock=None,
):
    start_ns = time.monotonic_ns()
    prefix = output_dir / f"turn-{index:03d}"
    input_path = prefix.with_suffix(".input.wav")
    received_path = prefix.with_suffix(".received.wav")
    played_path = prefix.with_suffix(".played.wav")
    if turn.audio is not None:
        shutil.copyfile(turn.audio, input_path)
    result = {
        "id": turn.id,
        "input_text": turn.input_text,
        "status": "running",
        "started_at_ns": start_ns,
        "events": [],
        "audio": {
            **({"input": str(input_path)} if turn.audio is not None else {}),
            "received": str(received_path),
            "played": str(played_path),
        },
        "playback_source": player_factory.source,
        "attribution": "server_session_and_listen_turn_with_audio_sequence",
        "listen_turn_id": index,
        "expected": turn.expect,
        "interruption": None,
        "requested_interruption": asdict(turn.interrupt) if turn.interrupt else None,
        "input_settings": asdict(scenario.input),
    }
    if turn.tool:
        result["tool"] = turn.tool
    if turn.sensor:
        result.update(sensor=turn.sensor, input_settings={"mode": "sensor"})
    sink = sink or events.put_nowait
    player = player_factory(sink, played_path)
    writer = None
    frames = 0
    input_done = server_done = playback_done = False
    quiet_deadline = None
    deadline = time.monotonic() + scenario.turn_timeout_seconds
    interrupt_task = None
    abort_requested_ns = None
    ack_deadline = None
    interrupt_late = False
    output_starts = {}
    played_starts = {}
    music_state = None

    def record_event(event):
        data = {k: v for k, v in event.data.items() if k != "pcm"}
        record = {
            "event": event.kind,
            "at_ns": event.at_ns,
            "wall_time_ns": client_wall_time(event.at_ns, client_clock),
            "offset_ms": (event.at_ns - start_ns) / 1e6,
            "data": data,
        }
        result["events"].append(record)
        return record

    async def interrupt_at(target_ns):
        await asyncio.sleep(max(0, (target_ns - time.monotonic_ns()) / 1e9))
        sink(Event("interrupt_due", {"target_at_ns": target_ns}))

    async def send_input():
        try:
            sink(Event("input_started"))
            if turn.sensor:
                data = await transport.send_sensor(turn.sensor)
            elif scenario.input.mode == "vad":
                uplink = prefix.with_suffix(".uplink.wav")
                result["audio"]["uplink"] = str(uplink)
                data = await transport.send_audio(
                    turn.audio, input_stream=scenario.input, uplink_path=uplink
                )
            else:
                data = await transport.send_audio(turn.audio)
            sink(Event("input_completed", data))
        except Exception as exc:
            sink(Event("error", {"message": f"input_failed: {exc}"}))

    input_task = asyncio.create_task(send_input())
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("turn_timeout")
            wake_at = min(
                value
                for value in (quiet_deadline, ack_deadline, deadline)
                if value is not None
            )
            remaining = wake_at - time.monotonic()
            try:
                event = await asyncio.wait_for(events.get(), timeout=max(0, remaining))
            except asyncio.TimeoutError:
                if quiet_deadline is not None and quiet_deadline <= deadline:
                    if turn.interrupt is not None and abort_requested_ns is None:
                        raise RuntimeError(
                            "interrupt_not_reached: response finished before the configured delay"
                        )
                    if interrupt_late:
                        raise RuntimeError("interrupt_trigger_late")
                    result["status"] = (
                        "interrupted" if abort_requested_ns is not None else "completed"
                    )
                    break
                if ack_deadline is not None and time.monotonic() >= ack_deadline:
                    raise TimeoutError("abort_ack_timeout")
                raise TimeoutError("turn_timeout")
            record = record_event(event)
            data = record["data"]
            if event.kind not in {
                "vas_event",
                "audio_frame_sent",
                "input_audio_frame_sent",
                "background_noise_started",
            }:
                quiet_deadline = None
            if event.kind == "pcm":
                pcm = event.data["pcm"]
                sample_rate = event.data["sample_rate"]
                if abort_requested_ns is None:
                    player.feed(
                        pcm,
                        sample_rate,
                        {
                            "audio_seq": event.data.get("audio_seq"),
                            "is_session_output": event.data.get(
                                "is_session_output", False
                            ),
                        },
                    )
                else:
                    result["interruption"]["audio_frames_after_abort"] += 1
                if writer is None:
                    writer = wave.open(str(received_path), "wb")
                    writer.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
                writer.writeframes(pcm)
                frames += 1
                record["event"] = "audio_received"
                data.update(
                    bytes=len(pcm), duration_ms=len(pcm) * 1000 / (sample_rate * 2)
                )
                data["discarded_after_abort"] = abort_requested_ns is not None
            elif event.kind == "tts_stop":
                if event.data.get("is_session_output") or event.data.get(
                    "response_listen_turn_id"
                ) not in (None, index):
                    # Keep hello audio in the session FIFO/WAV, but only the
                    # question's own reply may finish this turn or its player.
                    continue
                if abort_requested_ns is None:
                    server_done = True
                    player.finish()
                elif event.at_ns >= abort_requested_ns:
                    server_done = True
                    ack_deadline = None
                    result["interruption"].update(
                        server_stop_observed=True, server_stop_at_ns=event.at_ns
                    )
            elif event.kind == "input_completed":
                input_done = True
                if "server_listen_turn_id" in data:
                    result["server_listen_turn_id"] = data["server_listen_turn_id"]
            elif event.kind == "vas_event":
                row = event.data
                if row.get("event") == "audio_output_started":
                    output_starts[row["data"]["audio_seq"]] = row.get("output_kind")
            elif event.kind == "playback_frame_started":
                played_starts[event.data["audio_seq"]] = event.at_ns
            elif event.kind in {"music_playback_started", "music_playback_stopped"}:
                music_state = event.kind
            if turn.interrupt and interrupt_task is None:
                anchor_ns = None
                if (
                    turn.interrupt.anchor == "music_playback_started"
                    and event.kind == "music_playback_started"
                ):
                    anchor_ns = event.at_ns
                elif (
                    turn.interrupt.output_kind == "any"
                    and event.kind == "playback_started"
                ):
                    anchor_ns = event.at_ns
                else:
                    anchor_ns = next(
                        (
                            played_starts[seq]
                            for seq, kind in output_starts.items()
                            if kind == turn.interrupt.output_kind
                            and seq in played_starts
                        ),
                        None,
                    )
                if anchor_ns is not None:
                    target_ns = anchor_ns + int(
                        turn.interrupt.after_playback_seconds * 1e9
                    )
                    result["interruption"] = {
                        "after_playback_seconds": turn.interrupt.after_playback_seconds,
                        "playback_started_at_ns": anchor_ns,
                        "anchor": turn.interrupt.anchor,
                        "output_kind": turn.interrupt.output_kind,
                        "scheduled_at_ns": target_ns,
                        "client_playback_stopped": False,
                        "server_stop_observed": False,
                        "audio_frames_after_abort": 0,
                        "provider_cancellation": "unobserved",
                    }
                    interrupt_task = asyncio.create_task(interrupt_at(target_ns))
            if event.kind == "interrupt_due":
                if playback_done and turn.interrupt.output_kind != "music":
                    raise RuntimeError(
                        "interrupt_not_reached: playback already finished"
                    )
                abort_requested_ns = time.monotonic_ns()
                lateness = max(
                    0, (abort_requested_ns - event.data["target_at_ns"]) / 1e9
                )
                interrupt_late = lateness > turn.interrupt.max_lateness_seconds
                result["interruption"].update(
                    abort_requested_at_ns=abort_requested_ns,
                    trigger_lateness_ms=lateness * 1000,
                )
                record_event(Event("abort_requested", {}, abort_requested_ns))
                await asyncio.wait_for(
                    asyncio.gather(player.stop(), transport.abort()),
                    timeout=turn.interrupt.ack_timeout_seconds,
                )
                playback_done = True
                result["interruption"].update(
                    client_playback_stopped=True, client_stop_at_ns=time.monotonic_ns()
                )
                record_event(
                    Event(
                        "playback_stopped",
                        {"reason": "interrupt", "source": player.source},
                    )
                )
                server_done = False
                ack_deadline = min(
                    deadline, time.monotonic() + turn.interrupt.ack_timeout_seconds
                )
                record_event(Event("abort_sent"))
            elif event.kind == "playback_drained":
                playback_done = True
            elif event.kind in {"error", "disconnected"}:
                raise RuntimeError(event.data.get("message", event.kind))
            goal_met = (
                turn.completion_goal == "audio_completed"
                or music_state
                == "music_playback_"
                + ("started" if turn.completion_goal == "music_started" else "stopped")
            )
            if (
                input_done
                and server_done
                and playback_done
                and (frames or music_state)
                and goal_met
                and quiet_deadline is None
            ):
                quiet_deadline = time.monotonic() + scenario.settle_seconds
    except Exception as exc:
        result.update(status="failed", error=str(exc))
    finally:
        input_task.cancel()
        await asyncio.gather(input_task, return_exceptions=True)
        if interrupt_task is not None:
            interrupt_task.cancel()
            await asyncio.gather(interrupt_task, return_exceptions=True)
        await player.close()
        if writer is not None:
            writer.close()
        result["ended_at_ns"] = time.monotonic_ns()
        result["received_frames"] = frames
    return result
