import asyncio
import json
import time
import wave
from pathlib import Path

from voice_scenarios import Scenario, run_scenario
from voice_scenarios.protocol import Event


def audio_fixture(tmp_path, name="input.wav", duration=0.06):
    path = tmp_path / name
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x10" * int(duration * 16000))
    return path


class ScriptedVAS:
    """External VAS boundary: immediately sends all audio then tts/stop."""

    def __init__(self, replies=(0.08, 0.06)):
        self.replies = replies
        self.inputs = []
        self.abort_times = []
        self.closed = False

    async def connect(self, emit):
        self.emit = emit
        return {"session_id": "test-session", "transport": "scripted_vas"}

    async def send_audio(self, path):
        self.inputs.append({"path": str(path), "at_ns": time.monotonic_ns()})
        duration = self.replies[len(self.inputs) - 1]
        self.emit(Event("tts_start"))
        self.emit(
            Event(
                "pcm",
                {"pcm": b"\x00\x10" * int(16000 * duration), "sample_rate": 16000},
            )
        )
        self.emit(Event("tts_stop"))
        return {"frames": 1}

    async def abort(self):
        self.abort_times.append(time.monotonic_ns())
        self.emit(Event("tts_stop"))

    async def close(self):
        self.closed = True


def test_second_question_waits_for_playback_drain_after_server_stop(tmp_path):
    first = audio_fixture(tmp_path, "first.wav")
    second = audio_fixture(tmp_path, "second.wav")
    scenario = Scenario.from_dict(
        {
            "name": "sequential",
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 1,
            "evaluation": {
                "persona": "Zoomi（粉）",
                "knowledge_base_count": 4,
                "focus": "产品出图情况",
            },
            "turns": [
                {
                    "id": "first",
                    "audio": str(first),
                    "tool": "人设",
                    "input_text": "你是谁",
                },
                {"id": "second", "audio": str(second)},
            ],
        }
    )
    peer = ScriptedVAS()
    result = asyncio.run(run_scenario(scenario, peer, tmp_path / "result"))

    assert result["status"] == "passed"
    assert [turn["status"] for turn in result["turns"]] == ["completed", "completed"]
    first_events = result["turns"][0]["events"]
    playback_end = next(
        e["at_ns"] for e in first_events if e["event"] == "playback_drained"
    )
    assert peer.inputs[1]["at_ns"] >= playback_end
    assert result["turns"][0]["playback_source"] == "simulated_player"
    assert peer.closed
    saved = json.loads((tmp_path / "result" / "result.json").read_text())
    assert saved["status"] == "passed"
    assert saved["evaluation"] == scenario.evaluation
    assert saved["turns"][0]["tool"] == "人设"
    assert saved["evaluation_turns"][0] == {
        "id": "first",
        "tool": "人设",
        "input_text": "你是谁",
    }
    assert Path(saved["turns"][0]["audio"]["received"]).is_file()
    clock = saved["client_clock"]
    assert abs(clock["wall_time_ns"] - time.time_ns()) < 5_000_000_000
    journal = [
        json.loads(line)
        for line in (tmp_path / "result" / "client-events.jsonl")
        .read_text()
        .splitlines()
    ]
    for event in journal:
        assert event["wall_time_ns"] - clock["wall_time_ns"] == (
            event["monotonic_ns"] - clock["monotonic_ns"]
        )
    assert saved["turns"][0]["events"][0]["wall_time_ns"] is not None


def test_interrupt_after_playback_start_stops_buffer_and_then_runs_next_turn(tmp_path):
    first = audio_fixture(tmp_path, "first.wav")
    second = audio_fixture(tmp_path, "second.wav")
    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 1,
            "turns": [
                {
                    "id": "interrupt",
                    "audio": str(first),
                    "interrupt": {"after_playback_seconds": 0.05},
                },
                {"id": "continue", "audio": str(second)},
            ],
        }
    )
    peer = ScriptedVAS(replies=(0.3, 0.03))
    result = asyncio.run(run_scenario(scenario, peer, tmp_path / "result"))

    assert result["status"] == "passed"
    assert [turn["status"] for turn in result["turns"]] == ["interrupted", "completed"]
    interruption = result["turns"][0]["interruption"]
    assert interruption["server_stop_observed"] is True
    assert interruption["client_playback_stopped"] is True
    assert interruption["provider_cancellation"] == "unobserved"
    started = next(
        e["at_ns"]
        for e in result["turns"][0]["events"]
        if e["event"] == "playback_started"
    )
    assert 0.04 <= (peer.abort_times[0] - started) / 1e9 <= 0.2
    assert len(peer.abort_times) == 1
    assert peer.inputs[1]["at_ns"] > peer.abort_times[0]
    with wave.open(result["turns"][0]["audio"]["played"]) as audio:
        assert 0.03 <= audio.getnframes() / audio.getframerate() < 0.25


def test_answer_interrupt_uses_answer_frame_instead_of_filler_playback(tmp_path):
    class FillerVAS(ScriptedVAS):
        async def send_audio(self, path):
            self.emit(
                Event(
                    "vas_event",
                    {
                        "event": "audio_output_started",
                        "listen_turn_id": 1,
                        "output_kind": "filler",
                        "data": {"audio_seq": 1},
                    },
                )
            )
            self.emit(
                Event(
                    "pcm", {"pcm": b"\0\0" * 1600, "sample_rate": 16000, "audio_seq": 1}
                )
            )
            self.emit(
                Event(
                    "vas_event",
                    {
                        "event": "audio_output_started",
                        "listen_turn_id": 1,
                        "output_kind": "answer",
                        "data": {"audio_seq": 2},
                    },
                )
            )
            self.emit(
                Event(
                    "pcm", {"pcm": b"\0\0" * 4800, "sample_rate": 16000, "audio_seq": 2}
                )
            )
            self.emit(Event("tts_stop"))
            return {"frames": 1}

    scenario = Scenario.from_dict(
        {
            "turn_timeout_seconds": 1,
            "settle_seconds": 0.01,
            "turns": [
                {
                    "audio": str(audio_fixture(tmp_path)),
                    "interrupt": {
                        "after_ms": 40,
                        "anchor": "playback_started",
                        "output_kind": "answer",
                    },
                }
            ],
        }
    )
    peer = FillerVAS()
    result = asyncio.run(run_scenario(scenario, peer, tmp_path / "result"))
    assert result["status"] == "passed"
    first = next(
        e["at_ns"]
        for e in result["turns"][0]["events"]
        if e["event"] == "playback_started"
    )
    assert 0.13 < (peer.abort_times[0] - first) / 1e9 < 0.25
