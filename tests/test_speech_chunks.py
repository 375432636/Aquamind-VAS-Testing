"""Multi-chunk inputs are one client turn and never await the old answer."""

import asyncio
import json
import wave
from types import SimpleNamespace

import pytest

from voice_scenarios.ci_run import validate_turns
from voice_scenarios.model import Scenario
from voice_scenarios.websocket import WebSocketTransport


def test_saved_chunks_are_normalized_without_losing_gap():
    turns = validate_turns(
        json.dumps(
            [
                {
                    "chunks": [
                        {"text": "给我介绍"},
                        {"text": "一下红茶", "resume_after_endpoint_ms": 200},
                    ]
                }
            ]
        ),
        {"diagnostics": "frame", "turn_timeout_seconds": 90},
    )
    assert turns[0]["input_text"] == "给我介绍\n一下红茶"
    assert turns[0]["chunks"][1]["resume_after_endpoint_ms"] == 200
    with pytest.raises(ValueError):
        validate_turns(
            json.dumps(
                [
                    {
                        "chunks": [
                            {"text": "A"},
                            {"text": "B", "resume_after_endpoint_ms": -1},
                        ]
                    }
                ]
            ),
            {"diagnostics": "frame"},
        )


def test_prepared_chunks_load_as_one_turn(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"\0\0" * 960)
    scenario = Scenario.from_dict(
        {
            "turns": [
                {
                    "chunks": [
                        {"audio": "a.wav"},
                        {"audio": "a.wav", "resume_after_endpoint_ms": 200},
                    ]
                }
            ]
        },
        base_dir=tmp_path,
    )
    assert len(scenario.turns) == 1
    assert scenario.turns[0].chunks[1].resume_after_endpoint_ms == 200


def test_ptt_chunks_share_client_turn_and_send_distinct_start_stop(tmp_path):
    async def run():
        path = tmp_path / "a.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(b"\1\0" * 960)
        from voice_scenarios.model import AudioChunk, InputStream

        transport = WebSocketTransport("ws://unused", device_id="00:00:00:00:00:11")
        sent, events = [], []

        async def send(value):
            sent.append(value)

        transport.ws = SimpleNamespace(send=send)
        transport.emit = events.append
        metadata = await transport.send_audio_chunks(
            (AudioChunk(path), AudioChunk(path, 60)),
            input_stream=InputStream(),
            uplink_path=tmp_path / "sent.wav",
        )
        await transport.stop_input()
        controls = [json.loads(value) for value in sent if isinstance(value, str)]
        assert [item["state"] for item in controls] == [
            "start",
            "stop",
            "start",
            "stop",
        ]
        assert metadata["server_listen_turn_ids"] == [1, 2]
        assert transport.turn_sequence == 1
        assert transport.response_turns == {1: 1, 2: 1}
        assert sum(event.kind == "input_chunk_started" for event in events) == 2

    asyncio.run(run())


def test_vad_chunks_keep_uploading_noise_until_server_endpoint(tmp_path):
    async def run():
        from voice_scenarios.model import AudioChunk, InputStream

        path = tmp_path / "voice.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(b"\1\0" * 960)
        transport = WebSocketTransport(
            "ws://unused", device_id="00:00:00:00:00:11", diagnostics="frame"
        )
        sent, events = [], []

        async def send(value):
            sent.append(value)

        def emit(event):
            events.append(event)
            if event.kind == "input_chunk_finished":
                # The actual integration receives this via audio diagnostics.
                import time

                transport.speech_endpoints.put_nowait(
                    ({"listen_turn_id": 1}, time.monotonic_ns())
                )

        transport.ws = SimpleNamespace(send=send)
        transport.emit = emit
        metadata = await transport.send_audio_chunks(
            (AudioChunk(path), AudioChunk(path, 120)),
            input_stream=InputStream(mode="vad", pre_roll_seconds=0),
            uplink_path=tmp_path / "sent.wav",
        )
        await transport.stop_input()
        controls = [json.loads(value) for value in sent if isinstance(value, str)]
        assert [(item["state"], item.get("mode")) for item in controls] == [
            ("start", "auto")
        ]
        assert metadata["server_listen_turn_ids"] == [1]
        assert sum(event.kind == "input_chunk_started" for event in events) == 2
        assert (
            sum(
                event.kind == "input_audio_frame_sent" and not event.data["is_speech"]
                for event in events
            )
            >= 3
        )

    asyncio.run(run())
