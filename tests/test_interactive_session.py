import asyncio
import base64
import json
import wave

from voice_scenarios.interactive_session import InteractiveSession


def test_browser_audio_uses_browser_clock_and_keeps_session_turns(tmp_path):
    async def run():
        session = InteractiveSession(
            tmp_path, {"environment": "dev", "device_id": "00:00:00:00:00:21"}
        )

        def send(event, at, turn=0, **data):
            session.accept(
                {
                    "event": event,
                    "at_ns": at,
                    "wall_time_ns": 1700000000000000000 + at,
                    "turn": turn,
                    "data": data,
                }
            )

        send("connection_started", 1_000_000_000)
        send("turn_started", 2_000_000_000, 1, mode="text", text="你好")
        send("text_sent", 2_000_000_000, 1)
        pcm = base64.b64encode(b"\x01\x10" * 960).decode()
        send(
            "audio_received", 3_000_000_000, 1, pcm=pcm, audio_seq=1, sample_rate=16000
        )
        send(
            "playback_frame_started",
            3_300_000_000,
            1,
            pcm=pcm,
            audio_seq=1,
            sample_rate=16000,
            timing_model="browser_audio_context_v2",
            timing_method="output_timestamp",
            playback_observed_at_ns=3_360_000_000,
        )
        send("turn_finished", 3_360_000_000, 1)
        await session.finish(False, render=False)
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["connection_started_at_ns"] == 1_000_000_000
        assert result["playback_source"] == "browser_audio_context"
        assert result["turns"][0]["input_type"] == "text"
        assert result["turns"][0]["events"][-2]["at_ns"] == 3_300_000_000
        timing = result["turns"][0]["events"][-2]["data"]
        assert timing["timing_model"] == "browser_audio_context_v2"
        assert timing["playback_observed_at_ns"] == 3_360_000_000
        with wave.open(str(tmp_path / "turn-001.played.wav")) as w:
            assert w.readframes(960) == b"\x01\x10" * 960
        assert result["diagnostics"]["complete"] is False

    asyncio.run(run())


def test_browser_microphone_frames_survive_journal_merge_and_render(tmp_path):
    async def run():
        session = InteractiveSession(
            tmp_path, {"environment": "dev", "device_id": "00:00:00:00:00:21"}
        )

        def send(event, at, turn=0, **data):
            session.accept(
                dict(
                    event=event,
                    at_ns=at,
                    wall_time_ns=1700000000000000000 + at,
                    turn=turn,
                    data=data,
                )
            )

        send("connection_started", 1000000000)
        send("turn_started", 2000000000, 1, mode="vad")
        send(
            "input_audio_frame_sent",
            2000000000,
            1,
            pcm=base64.b64encode(b"\x01\x10" * 960).decode(),
            sample_rate=16000,
            pcm_offset_samples=0,
            stream="uplink",
        )
        send("speech_input_finished", 2060000000, 1, source="client_energy_estimate")
        send("turn_finished", 3000000000, 1)
        report = await session.finish()
        assert report["session_playback"]["status"] == "ready"
        assert (
            report["session_playback"]["turns"][0]["input_segments"][0]["timing"]
            == "recorded"
        )
        assert not report["session_playback"]["limitations"]
        with wave.open(str(tmp_path / "turn-001.input.wav")) as audio:
            assert audio.readframes(960) == b"\x01\x10" * 960

    asyncio.run(run())


def test_silent_browser_microphone_is_retained_but_not_reported_as_success(tmp_path):
    async def run():
        session = InteractiveSession(
            tmp_path, {"environment": "dev", "device_id": "00:00:00:00:00:21"}
        )
        session.accept(
            dict(event="turn_started", at_ns=1, turn=1, data={"mode": "manual"})
        )
        session.accept(
            dict(
                event="input_audio_frame_sent",
                at_ns=2,
                turn=1,
                data={
                    "pcm": base64.b64encode(bytes(1920)).decode(),
                    "sample_rate": 16000,
                    "pcm_offset_samples": 0,
                    "stream": "input",
                },
            )
        )
        session.accept(dict(event="turn_finished", at_ns=3, turn=1, data={}))
        await session.finish(render=False)
        assert session.result["turns"][0]["status"] == "failed"
        assert session.result["turns"][0]["input_capture"]["peak"] == 0
        assert "静音" in session.result["turns"][0]["error"]
        assert (tmp_path / "turn-001.input.wav").exists()

    asyncio.run(run())


def test_browser_turn_without_any_captured_audio_is_not_reported_as_success(tmp_path):
    async def run():
        session = InteractiveSession(
            tmp_path, {"environment": "dev", "device_id": "00:00:00:00:00:21"}
        )
        session.accept(
            dict(event="turn_started", at_ns=1, turn=1, data={"mode": "manual"})
        )
        session.accept(dict(event="turn_finished", at_ns=2, turn=1, data={}))
        await session.finish(render=False)
        assert session.result["turns"][0]["status"] == "failed"
        assert "未收到麦克风音频" in session.result["turns"][0]["error"]

    asyncio.run(run())
