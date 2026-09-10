import asyncio
import json
import math
import struct
import time
import wave

import opuslib_next as opuslib
import pytest
from websockets.asyncio.server import serve

from voice_scenarios import Scenario, run_scenario


def test_vad_requires_explicit_uplink_artifact_path(tmp_path):
    from voice_scenarios.model import InputStream
    from voice_scenarios.websocket import WebSocketTransport

    transport = WebSocketTransport("ws://unused", device_id="test")
    with pytest.raises(ValueError, match="uplink_path"):
        asyncio.run(
            transport.send_audio(
                tmp_path / "input.wav", input_stream=InputStream(mode="vad")
            )
        )


def test_vad_continues_nonzero_noise_without_stop_on_same_session(tmp_path):
    from voice_scenarios.websocket import WebSocketTransport

    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(
            struct.pack(
                "<4800h",
                *[
                    int(8000 * math.sin(i * 2 * math.pi * 440 / 16000))
                    for i in range(4800)
                ],
            )
        )
    scenario = Scenario.from_dict(
        {
            "input": {"mode": "vad", "pre_roll_seconds": 0.12, "noise_dbfs": -55},
            "turn_timeout_seconds": 4,
            "settle_seconds": 0.08,
            "turns": [{"audio": str(source)}, {"audio": str(source)}],
        }
    )
    controls, packets, endpoints = [], [], []

    async def exercise():
        async def peer(ws):
            decoder = opuslib.Decoder(16000, 1)
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            active = False
            quiet = 0
            async for raw in ws:
                if isinstance(raw, bytes):
                    pcm = decoder.decode(raw, 960)
                    values = struct.unpack("<960h", pcm)
                    rms = math.sqrt(sum(v * v for v in values) / 960)
                    packets.append((time.monotonic(), rms))
                    if rms > 1000:
                        active, quiet = True, 0
                    elif active:
                        quiet += 1
                        if quiet >= 3:
                            active = False
                            endpoints.append(len(packets))
                            await ws.send(json.dumps({"type": "tts", "state": "start"}))
                            for _ in range(6):
                                await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                            await ws.send(json.dumps({"type": "tts", "state": "stop"}))
                    continue
                message = json.loads(raw)
                controls.append(message)
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "continuous",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", device_id="test"
            )
            return await run_scenario(scenario, transport, tmp_path / "report")

    result = asyncio.run(exercise())
    assert result["status"] == "passed", result
    assert len(endpoints) == 2
    assert sum(c["type"] == "hello" for c in controls) == 1
    listens = [c for c in controls if c["type"] == "listen"]
    assert listens == [{"type": "listen", "state": "start", "mode": "auto"}] * 2
    assert len(packets) - endpoints[-1] >= 5  # microphone continues during playback
    assert all(0 < rms < 300 for _, rms in packets[-4:])
    assert max(b[0] - a[0] for a, b in zip(packets, packets[1:])) < 0.18
    for turn in result["turns"]:
        assert any(e["event"] == "speech_input_finished" for e in turn["events"])
        assert not any(e["event"] == "listen_stop_sent" for e in turn["events"])
        with wave.open(turn["audio"]["uplink"]) as uplink:
            assert uplink.getnframes() > 4800 + 16000 * 0.4


@pytest.mark.parametrize(
    "settings",
    [
        {"mode": "invalid"},
        {"mode": "vad", "noise_dbfs": float("nan")},
        {"mode": "vad", "noise_dbfs": 1},
        {"mode": "vad", "pre_roll_seconds": -1},
    ],
)
def test_invalid_input_settings_rejected(tmp_path, settings):
    path = tmp_path / "input.wav"
    path.touch()
    with pytest.raises(ValueError):
        Scenario.from_dict({"input": settings, "turns": [{"audio": str(path)}]})


def test_noise_only_times_out_and_closes_microphone_without_false_success(tmp_path):
    from voice_scenarios.websocket import WebSocketTransport

    source = tmp_path / "quiet.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"\0\0" * 1920)
    scenario = Scenario.from_dict(
        {
            "input": {"mode": "vad", "pre_roll_seconds": 0},
            "turn_timeout_seconds": 0.55,
            "turns": [{"audio": str(source)}],
        }
    )
    controls, packets = [], []

    async def exercise():
        async def peer(ws):
            async for raw in ws:
                if isinstance(raw, bytes):
                    packets.append(raw)
                else:
                    message = json.loads(raw)
                    controls.append(message)
                    if message["type"] == "hello":
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "hello",
                                    "session_id": "quiet",
                                    "audio_params": {
                                        "format": "opus",
                                        "sample_rate": 16000,
                                        "channels": 1,
                                    },
                                }
                            )
                        )

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                device_id="quiet",
            )
            result = await run_scenario(scenario, transport, tmp_path / "output")
            assert transport.microphone_task is None
            assert transport.reader.done()
            return result

    result = asyncio.run(exercise())
    assert result["status"] == "failed"
    assert result["turns"][0]["error"] == "turn_timeout"
    assert len(packets) >= 8
    assert not any(m.get("state") == "stop" for m in controls)
    with wave.open(result["turns"][0]["audio"]["uplink"]) as saved:
        assert saved.getnframes() == len(packets) * 960
