"""A saved text session must use VAS's native listen/detect path."""

import asyncio
import json

import opuslib_next as opuslib
from websockets.asyncio.server import serve

from voice_scenarios import Scenario, run_scenario
from voice_scenarios.ci_run import prepare
from voice_scenarios.turn_attribution import attribute_text_turns
from voice_scenarios.websocket import WebSocketTransport


def test_text_mode_prepares_without_speech_synthesis(tmp_path, monkeypatch):
    def unexpected_synthesis(*_args):
        raise AssertionError("text mode must not synthesize input audio")

    monkeypatch.setattr("voice_scenarios.ci_run.synthesize", unexpected_synthesis)
    settings, scenario = prepare(
        tmp_path,
        {
            "VAS_ENVIRONMENT": "dev",
            "VAS_DEVICE_ID": "AA:BB:CC:DD:EE:91",
            "VAS_INPUT_MODE": "text",
            "VAS_TURNS_JSON": json.dumps([{"text": "带我到 A 区。"}]),
        },
    )
    assert settings["input_mode"] == "text"
    assert scenario.input.mode == "text"
    assert scenario.turns[0].text == "带我到 A 区。"
    assert not list(tmp_path.rglob("*.wav"))


def test_text_turn_uses_native_vas_detect_frame_without_uplink_audio(tmp_path):
    text = "带我到 A 区。"
    scenario = Scenario.from_dict(
        {
            "input": {"mode": "text"},
            "greeting_wait_seconds": 0.01,
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 3,
            "turns": [{"text": text}],
        }
    )
    sent = []

    async def exercise():
        async def peer(ws):
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            async for raw in ws:
                sent.append(raw)
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "text-session",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )
                elif message["type"] == "listen":
                    await ws.send(json.dumps({"type": "stt", "text": message["text"]}))
                    await ws.send(json.dumps({"type": "tts", "state": "start"}))
                    await ws.send(
                        json.dumps(
                            {"type": "tts", "state": "sentence_start", "text": "好的"}
                        )
                    )
                    await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/",
                device_id="AA:BB:CC:DD:EE:91",
            )
            return await run_scenario(scenario, transport, tmp_path / "output")

    result = asyncio.run(exercise())
    assert result["status"] == "passed", result
    frames = [json.loads(raw) for raw in sent if isinstance(raw, str)]
    assert [message for message in frames if message["type"] == "listen"] == [
        {"type": "listen", "mode": "manual", "state": "detect", "text": text}
    ]
    assert not any(isinstance(raw, bytes) for raw in sent)
    assert result["turns"][0]["input_settings"]["mode"] == "text"


def test_text_diagnostics_follow_ordered_detect_boundaries():
    report = {
        "turns": [
            {"input_type": "text", "events": [{"event": "text_sent"}]},
            {"input_type": "text", "events": [{"event": "text_sent"}]},
        ]
    }
    events = [
        {"seq": 1, "event": "tts_state_sent", "listen_turn_id": None},
        {"seq": 10, "event": "listen_detect_received", "listen_turn_id": None},
        {"seq": 11, "event": "robot_output_evaluated", "listen_turn_id": None},
        {"seq": 20, "event": "listen_detect_received", "listen_turn_id": None},
        {"seq": 21, "event": "robot_output_evaluated", "listen_turn_id": None},
    ]
    attributed, failures = attribute_text_turns(report, events)
    assert not failures
    assert [event["client_turn_index"] for event in attributed] == [0, 1, 1, 2, 2]
    assert [event["listen_turn_id"] for event in attributed] == [None] * 5
