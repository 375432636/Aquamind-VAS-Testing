import asyncio
import json
import wave

import opuslib_next as opuslib
import pytest
from websockets.asyncio.server import serve

from voice_scenarios import Scenario, run_scenario
from voice_scenarios.websocket import WebSocketTransport


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
