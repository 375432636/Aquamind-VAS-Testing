import asyncio
import json
import wave

import opuslib_next as opuslib
import pytest
from websockets.asyncio.server import serve

from voice_scenarios import Scenario, run_scenario
from voice_scenarios.websocket import WebSocketTransport


@pytest.mark.parametrize("diagnostics", ["off", "stage", "frame"])
def test_input_frame_clock_records_unpadded_samples_at_send_completion(
    tmp_path, diagnostics
):
    source = tmp_path / "partial.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"\x00\x10" * 1100)
    events, sent = [], []

    class Socket:
        async def send(self, packet):
            sent.append(packet)

    transport = WebSocketTransport(
        "ws://unused", device_id="test", diagnostics=diagnostics
    )
    transport.ws = Socket()
    transport.emit = events.append
    asyncio.run(transport.send_audio(source))
    frames = [event for event in events if event.kind == "input_audio_frame_sent"]
    assert (
        len(frames)
        == len([packet for packet in sent if isinstance(packet, bytes)])
        == 2
    )
    assert [event.data["pcm_offset_samples"] for event in frames] == [0, 960]
    assert [event.data["samples"] for event in frames] == [960, 140]
    assert all(event.data["stream"] == "input" for event in frames)
    assert all(event.data["sample_rate"] == 16000 for event in frames)
    assert all(
        event.data["is_speech"] and event.data["listen_turn_id"] == 1
        for event in frames
    )
    assert frames[1].at_ns > frames[0].at_ns
    assert frames[0].at_ns == next(
        event.at_ns for event in events if event.kind == "first_audio_sent"
    )
    assert frames[-1].at_ns <= next(
        event.at_ns for event in events if event.kind == "listen_stop_sent"
    )


def test_real_websocket_opus_round_trip_and_timed_abort(tmp_path):
    source = tmp_path / "input.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x10" * 1920)
    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.02,
            "turn_timeout_seconds": 3,
            "turns": [
                {"audio": str(source), "interrupt": {"after_playback_seconds": 0.1}},
                {"audio": str(source)},
            ],
        }
    )
    observed = []

    async def exercise():
        async def peer(ws):
            decoder = opuslib.Decoder(16000, 1)
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            async for raw in ws:
                if isinstance(raw, bytes):
                    observed.append(("input_audio", len(decoder.decode(raw, 960))))
                    continue
                message = json.loads(raw)
                observed.append((message["type"], message.get("state")))
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "wire-session",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                    "frame_duration": 60,
                                },
                            }
                        )
                    )
                elif message["type"] == "listen" and message["state"] == "stop":
                    await ws.send(json.dumps({"type": "stt", "text": "测试问题"}))
                    await ws.send(json.dumps({"type": "tts", "state": "start"}))
                    for _ in range(8):
                        await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))
                elif message["type"] == "abort":
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))

        async with serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{port}/xiaozhi/v1/", device_id="test-device"
            )
            return await run_scenario(scenario, transport, tmp_path / "output")

    result = asyncio.run(exercise())
    assert result["status"] == "passed", result
    assert [t["status"] for t in result["turns"]] == ["interrupted", "completed"]
    assert result["session"]["session_id"] == "wire-session"
    assert observed.count(("hello", None)) == 1
    assert observed.count(("abort", None)) == 1
    assert observed.count(("input_audio", 1920)) == 4
    assert all(t["received_frames"] == 8 for t in result["turns"])


@pytest.mark.parametrize("diagnostics", ["off", "stage", "frame"])
@pytest.mark.parametrize("greeting_timing", ["before_input", "after_input"])
def test_startup_greeting_does_not_complete_question_or_shift_later_replies(
    tmp_path, diagnostics, greeting_timing
):
    """Exercise real Opus/WebSocket ordering, including delayed diagnostic batches."""
    import time

    from voice_scenarios.reply_audio import prepare_reply_audio
    from voice_scenarios.report import _timing_values, evaluate
    from voice_scenarios.session_timing import prepare_session_playback

    source = tmp_path / "question.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"\x00\x10" * 1920)
    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 3,
            "turns": [
                {"id": "first", "audio": str(source)},
                {"id": "followup", "audio": str(source)},
            ],
        }
    )
    input_starts, diagnostic_rows = [], []

    async def exercise():
        async def peer(ws):
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            audio_sequence = 0
            turn = 0
            tasks = []

            async def output(text, owner, frame_count):
                nonlocal audio_sequence
                # Production VAS may label the greeting as answer/None. The
                # client must retain its independent wire classification.
                diagnostic_rows.append(
                    {
                        "server_session_id": "greeting-session",
                        "seq": len(diagnostic_rows) + 1,
                        "event": "audio_output_started",
                        "clock_id": "vas-process",
                        "monotonic_ns": time.monotonic_ns(),
                        "listen_turn_id": owner,
                        "output_kind": "answer",
                        "data": {"audio_seq": audio_sequence + 1},
                    }
                )
                await ws.send(json.dumps({"type": "tts", "state": "start"}))
                await ws.send(
                    json.dumps({"type": "tts", "state": "sentence_start", "text": text})
                )
                for _ in range(frame_count):
                    await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                    audio_sequence += 1
                await ws.send(json.dumps({"type": "tts", "state": "stop"}))

            async def reply(owner):
                await asyncio.sleep(0.30)
                await ws.send(json.dumps({"type": "stt", "text": f"问题 {owner}"}))
                # send_stt_message emits start, and the first TTS emits it
                # again. Duplicate starts are part of the actual VAS wire.
                await ws.send(json.dumps({"type": "tts", "state": "start"}))
                await output(f"回答 {owner}", owner, 4)

            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    message = json.loads(raw)
                    if message["type"] == "diagnostics":
                        if message["state"] == "start":
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "diagnostics",
                                        "state": "started",
                                        "schema_version": 1,
                                        "server_session_id": "greeting-session",
                                        "level": diagnostics,
                                    }
                                )
                            )
                        else:
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "diagnostics",
                                        "state": "events",
                                        "schema_version": 1,
                                        "server_session_id": "greeting-session",
                                        "events": diagnostic_rows,
                                        "next_seq": len(diagnostic_rows),
                                        "end_seq": len(diagnostic_rows),
                                        "gap": False,
                                        "complete": True,
                                        "finished": True,
                                    }
                                )
                            )
                    elif message["type"] == "hello":
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "hello",
                                    "session_id": "greeting-session",
                                    "audio_params": {
                                        "format": "opus",
                                        "sample_rate": 16000,
                                        "channels": 1,
                                    },
                                }
                            )
                        )
                        if greeting_timing == "before_input":
                            await output("欢迎来到门店", None, 3)
                    elif message["type"] == "listen":
                        if message["state"] == "start":
                            input_starts.append(time.monotonic_ns())
                            turn += 1
                        else:
                            if turn == 1 and greeting_timing == "after_input":
                                await output("欢迎来到门店", None, 3)
                            tasks.append(asyncio.create_task(reply(turn)))
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/xiaozhi/v1/",
                device_id="test-device",
                diagnostics=diagnostics,
            )
            return await run_scenario(scenario, transport, tmp_path / "output")

    result = asyncio.run(exercise())
    assert result["status"] == "passed", result
    report = evaluate(result, diagnostic_rows if diagnostics != "off" else [])
    assert report["status"] == "passed", report["failures"]
    assert [turn["metrics"]["asr_text"] for turn in report["turns"]] == [
        "问题 1",
        "问题 2",
    ]
    assert all(turn["metrics"]["first_playback_ms"] >= 250 for turn in report["turns"])
    first_events = result["turns"][0]["events"]
    drained = next(e["at_ns"] for e in first_events if e["event"] == "playback_drained")
    assert input_starts[1] >= drained
    first_frames = [e for e in first_events if e["event"] == "playback_frame_started"]
    if greeting_timing == "before_input":
        assert result["startup"]["status"] == "completed"
        assert input_starts[0] >= result["startup"]["playback_drained_at_ns"]
        greeting_frames = [
            e
            for e in result["startup"]["events"]
            if e["event"] == "playback_frame_started"
        ]
        assert len(greeting_frames) == 3
        assert all(e["data"].get("is_session_output") for e in greeting_frames)
        assert len(first_frames) == 4
        assert all(not e["data"].get("is_session_output") for e in first_frames)
        with wave.open(result["startup"]["audio"]["played"]) as audio:
            assert audio.getnframes() == 3 * 960
    else:
        assert len(first_frames) == 7
        assert all(e["data"].get("is_session_output") for e in first_frames[:3])
        assert all(not e["data"].get("is_session_output") for e in first_frames[3:])
    with wave.open(result["turns"][0]["audio"]["played"]) as audio:
        assert audio.getnframes() == len(first_frames) * 960
    prepare_reply_audio(report, tmp_path / "output")
    if greeting_timing == "after_input":
        greeting = report["turns"][0]["reply_timing"]["sentences"][0]
        assert (greeting["kind"], greeting["text"]) == ("greeting", "欢迎来到门店")
        assert greeting["audio"]["played"]["status"] == "ready"
        assert greeting["audio"]["played"]["samples"] == 3 * 960
    for turn in report["turns"]:
        assert _timing_values(turn)[0] == pytest.approx(
            turn["metrics"]["first_playback_ms"] / 1000
        )
    session = prepare_session_playback(report, tmp_path / "output")
    assert session["status"] == "ready", session["limitations"]
    assert [
        (s["kind"], s["text"]) for s in session["segments"] if s["kind"] == "greeting"
    ] == [("greeting", "欢迎来到门店")]
    waits = [wait for wait in session["waits"] if wait["kind"] == "first_reply"]
    assert len(waits) == 2
    assert all(wait["duration_seconds"] >= 0.25 for wait in waits)


def test_first_question_interrupt_waits_for_real_reply_after_long_greeting(tmp_path):
    import time

    source = tmp_path / "question.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"\x00\x10" * 1920)
    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 3,
            "turns": [
                {
                    "audio": str(source),
                    "interrupt": {"after_playback_seconds": 0.10},
                },
                {"audio": str(source)},
            ],
        }
    )
    aborts = []

    async def exercise():
        async def peer(ws):
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            turn = 0

            async def output(text, count):
                await ws.send(json.dumps({"type": "tts", "state": "start"}))
                await ws.send(
                    json.dumps({"type": "tts", "state": "sentence_start", "text": text})
                )
                for _ in range(count):
                    await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                await ws.send(json.dumps({"type": "tts", "state": "stop"}))

            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "greeting-interrupt",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )
                    await output("欢迎来到门店，给你介绍一下这里", 12)
                elif message["type"] == "listen" and message["state"] == "stop":
                    turn += 1
                    await ws.send(json.dumps({"type": "stt", "text": f"问题 {turn}"}))
                    await output(f"回答 {turn}", 8 if turn == 1 else 2)
                elif message["type"] == "abort":
                    aborts.append(time.monotonic_ns())
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/xiaozhi/v1/",
                device_id="test-device",
            )
            return await run_scenario(scenario, transport, tmp_path / "output")

    result = asyncio.run(exercise())
    assert result["status"] == "passed", result
    assert [turn["status"] for turn in result["turns"]] == ["interrupted", "completed"]
    first = result["turns"][0]
    greeting_frames = [
        event
        for event in result["startup"]["events"]
        if event["event"] == "playback_frame_started"
        and event["data"].get("is_session_output")
    ]
    assert len(greeting_frames) == 12
    reply_start = next(
        event["at_ns"]
        for event in first["events"]
        if event["event"] == "playback_started"
    )
    assert reply_start >= greeting_frames[-1]["at_ns"] + 55_000_000
    assert len(aborts) == 1
    assert 0.08 <= (aborts[0] - reply_start) / 1e9 <= 0.3
    assert first["interruption"]["server_stop_observed"]


def test_diagnostics_share_websocket_without_token_headers_or_http(
    tmp_path, monkeypatch
):
    import aiohttp

    from voice_scenarios.diagnostics import DiagnosticCollector

    def reject_http(*args, **kwargs):
        raise AssertionError("diagnostics must not open an HTTP connection")

    monkeypatch.setattr(aiohttp, "ClientSession", reject_http)

    async def exercise():
        async def peer(ws):
            assert "X-VAS-Diagnostics-Token" not in ws.request.headers
            assert "X-VAS-Diagnostics" not in ws.request.headers
            start = json.loads(await ws.recv())
            assert start == {"type": "diagnostics", "state": "start", "level": "stage"}
            await ws.send(
                json.dumps(
                    {
                        "type": "diagnostics",
                        "state": "started",
                        "schema_version": 1,
                        "server_session_id": "ws-only",
                        "level": "stage",
                    }
                )
            )
            assert json.loads(await ws.recv())["type"] == "hello"
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "session_id": "ws-only",
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    }
                )
            )
            assert json.loads(await ws.recv()) == {
                "type": "diagnostics",
                "state": "finish",
            }
            rows = [
                {
                    "server_session_id": "ws-only",
                    "seq": i,
                    "event": name,
                    "monotonic_ns": i * 100,
                }
                for i, name in enumerate(
                    ["capture_started", "memory_request_finished", "capture_finished"],
                    1,
                )
            ]
            await ws.send(
                json.dumps(
                    {
                        "type": "diagnostics",
                        "state": "events",
                        "schema_version": 1,
                        "server_session_id": "ws-only",
                        "events": rows,
                        "next_seq": 3,
                        "gap": False,
                        "complete": True,
                        "finished": True,
                        "end_seq": 3,
                    }
                )
            )
            await ws.close()

        collector = DiagnosticCollector(tmp_path)

        def emit(event):
            if event.kind == "diagnostics_started":
                collector.bind_session(event.data["server_session_id"])
            elif event.kind == "diagnostics":
                collector.accept(event.data)

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/xiaozhi/v1/",
                device_id="test-device",
                diagnostics="stage",
            )
            session = await transport.connect(emit)
            assert session["session_id"] == "ws-only"
            await transport.close()
            outcome = await collector.finish()
            assert outcome["complete"] and outcome["finished"]
            assert collector.events[1]["event"] == "memory_request_finished"

    asyncio.run(exercise())


def test_server_rejecting_diagnostics_fails_before_sending_audio():
    async def exercise():
        import gc

        background_errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: background_errors.append(context)
        )

        async def peer(ws):
            assert json.loads(await ws.recv())["type"] == "diagnostics"
            await ws.send(
                json.dumps(
                    {"type": "diagnostics", "state": "error", "error": "unsupported"}
                )
            )
            await ws.close()

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                device_id="test-device",
                diagnostics="stage",
            )
            try:
                with pytest.raises(ValueError, match="diagnostics_rejected"):
                    await transport.connect(lambda event: None)
            finally:
                await transport.close()
            del transport
            gc.collect()
            await asyncio.sleep(0)
            assert not background_errors

    asyncio.run(exercise())
