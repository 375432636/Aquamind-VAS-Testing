"""Persist browser-owned events; the Python server never acts as its speaker."""

import base64
import json
import struct
import wave
from pathlib import Path

from .diagnostics import DiagnosticCollector
from .runner import save_result


class InteractiveSession:
    def __init__(self, directory, config):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.result = dict(
            schema_version=1,
            name="网页实时对话",
            status="running",
            turns=[],
            playback_source="browser_audio_context",
            evaluation={
                "persona": "实时对话",
                "focus": "交互与延迟",
                "knowledge_base_count": None,
            },
            run_metadata={
                k: config[k] for k in ("environment", "device_id") if k in config
            },
            startup=dict(events=[], audio={}, received_frames=0, status="not_observed"),
        )
        self.journal = (self.directory / "client-events.jsonl").open("w")
        self.collector = DiagnosticCollector(self.directory)
        self.writers = {}
        self.closed = False
        self.finished_report = None

    def _audio(self, target, index, kind, pcm, rate):
        key = (index, kind)
        if key not in self.writers:
            name = f"turn-{index:03d}.{kind}.wav" if index else f"greeting.{kind}.wav"
            writer = wave.open(str(self.directory / name), "wb")
            writer.setparams((1, 2, rate, 0, "NONE", ""))
            self.writers[key] = (writer, rate)
            target["audio"][kind] = name
        writer, expected = self.writers[key]
        if expected != rate:
            raise ValueError("audio_format_changed_within_turn")
        writer.writeframes(pcm)

    def accept(self, event):
        if self.closed:
            raise ValueError("session_already_finished")
        name, at, data = event["event"], event["at_ns"], dict(event.get("data", {}))
        index = event.get("turn", 0)
        if (
            not isinstance(at, int)
            or at < 0
            or not isinstance(index, int)
            or not 0 <= index <= 1000
        ):
            raise ValueError("invalid_browser_event")
        if name == "turn_started":
            if index != len(self.result["turns"]) + 1:
                raise ValueError("turn_sequence_gap")
            mode = data.get("mode", "manual")
            self.result["turns"].append(
                dict(
                    id=f"turn-{index:03d}",
                    listen_turn_id=index,
                    input_type="text" if mode == "text" else "audio",
                    input_text=data.get("text", ""),
                    input_settings={"mode": mode},
                    status="running",
                    started_at_ns=at,
                    events=[],
                    audio={},
                    received_frames=0,
                    expected={},
                    interruption={},
                    playback_source="browser_audio_context",
                )
            )
        if index > len(self.result["turns"]):
            raise ValueError("unknown_turn")
        target = self.result["turns"][index - 1] if index else self.result["startup"]
        if "pcm" in data:
            pcm = base64.b64decode(data.pop("pcm"), validate=True)
            rate = data.get("sample_rate")
            if (
                not pcm
                or len(pcm) % 2
                or len(pcm) > 192000
                or rate not in (8000, 12000, 16000, 24000, 48000)
            ):
                raise ValueError("invalid_pcm")
            kinds = {
                "audio_received": "received",
                "playback_frame_started": "played",
                "input_audio_frame_sent": "input",
            }
            if name not in kinds:
                raise ValueError("unexpected_pcm_event")
            self._audio(target, index, kinds[name], pcm, rate)
            data.update(bytes=len(pcm), duration_ms=len(pcm) / rate / 2 * 1000)
            if name == "input_audio_frame_sent":
                capture = target.setdefault("input_capture", {"samples": 0, "peak": 0})
                capture["samples"] += len(pcm) // 2
                capture["peak"] = max(
                    capture["peak"],
                    max(abs(value[0]) for value in struct.iter_unpack("<h", pcm)),
                )
                target["audio"]["uplink"] = target["audio"]["input"]
                data.update(samples=len(pcm) // 2, listen_turn_id=index)
            if name == "audio_received":
                target["received_frames"] += 1
            if name == "playback_frame_started":
                data["source"] = "browser_audio_context"
                data.setdefault("timing_model", "browser_audio_context_v1")
        if name == "connection_started":
            self.result.update(
                connection_started_at_ns=at,
                client_clock={
                    "monotonic_ns": at,
                    "wall_time_ns": event["wall_time_ns"],
                },
            )
        if name == "hello_received":
            self.result["session"] = data
            self.collector.bind_session(data["session_id"])
        if name == "diagnostics_started":
            self.collector.bind_session(data["server_session_id"])
        if name == "diagnostics":
            self.collector.accept(data)
        if name == "stt" and index and target["input_type"] != "text":
            target["input_text"] = data.get("text", "")
        if name == "turn_finished":
            target.update(
                status="interrupted" if data.get("interrupted") else "completed",
                ended_at_ns=at,
            )
        if name == "playback_drained" and not index:
            target.update(status="completed", playback_drained_at_ns=at)
        if name == "playback_stopped" and index:
            target["interruption"].update(abort_requested_ns=at)
        record = dict(event=name, at_ns=at, data=data)
        if name != "diagnostics":
            target["events"].append(record)
        self.journal.write(
            json.dumps(
                dict(
                    record,
                    source="browser",
                    clock_id="browser-session",
                    wall_time_ns=event.get("wall_time_ns"),
                    monotonic_ns=at,
                ),
                ensure_ascii=False,
            )
            + "\n"
        )
        self.journal.flush()
        if name in {"turn_started", "turn_finished", "hello_received"}:
            save_result(self.result, self.directory)

    async def finish(self, complete=True, *, render=True):
        if self.closed:
            return self.finished_report
        self.closed = True
        for writer, _ in self.writers.values():
            writer.close()
        self.journal.close()
        self.result["diagnostics"] = await self.collector.finish()
        for turn in [self.result["startup"], *self.result["turns"]]:
            turn["events"].sort(key=lambda e: e["at_ns"])
            if turn.get("input_type") == "audio":
                capture = turn.get("input_capture", {})
                if not capture.get("samples"):
                    turn.update(
                        status="failed",
                        error="未收到麦克风音频，请确认麦克风已就绪后再说话",
                    )
                elif capture["peak"] == 0:
                    turn.update(
                        status="failed",
                        error="麦克风只录到静音（全零采样），请检查输入设备",
                    )
            if turn.get("status") == "running":
                turn.update(
                    status="failed", error="browser_disconnected_before_turn_end"
                )
        self.result["status"] = (
            "passed"
            if complete and all(t["status"] != "failed" for t in self.result["turns"])
            else "failed"
        )
        save_result(self.result, self.directory)
        if render:
            from .__main__ import create_report
            from .excel_report import export_excel

            self.finished_report = create_report(self.directory)
            export_excel([self.finished_report], self.directory / "evaluation.xlsx")
        return self.finished_report
