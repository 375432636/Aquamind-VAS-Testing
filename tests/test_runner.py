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


def test_first_input_waits_for_greeting_stop_and_playback_drain(tmp_path):
    class GreetingVAS(ScriptedVAS):
        async def connect(self, emit):
            session = await super().connect(emit)
            self.greeting_started = time.monotonic_ns()
            emit(Event("tts_start", {"is_session_output": True}))
            emit(
                Event(
                    "pcm",
                    {
                        "pcm": b"\x00\x10" * 4800,
                        "sample_rate": 16000,
                        "audio_seq": 1,
                        "is_session_output": True,
                    },
                )
            )
            emit(Event("tts_stop", {"is_session_output": True}))
            return session

    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 1,
            "turns": [{"id": "first", "audio": str(audio_fixture(tmp_path))}],
        }
    )
    peer = GreetingVAS(replies=(0.06,))
    result = asyncio.run(run_scenario(scenario, peer, tmp_path / "greeting"))
    assert result["status"] == "passed", result
    assert peer.inputs[0]["at_ns"] >= peer.greeting_started + 300_000_000
    assert result["startup"]["status"] == "completed"
    assert result["startup"]["playback_drained_at_ns"] <= peer.inputs[0]["at_ns"]


def test_delayed_greeting_is_waited_for_before_first_input(tmp_path):
    class DelayedGreetingVAS(ScriptedVAS):
        async def connect(self, emit):
            session = await super().connect(emit)
            self.task = asyncio.create_task(self.greet())
            return session

        async def greet(self):
            await asyncio.sleep(0.03)
            self.emit(Event("tts_start", {"is_session_output": True}))
            self.emit(
                Event(
                    "pcm",
                    {
                        "pcm": b"\x00\x10" * 1600,
                        "sample_rate": 16000,
                        "is_session_output": True,
                    },
                )
            )
            self.emit(Event("tts_stop", {"is_session_output": True}))
            self.greeting_at = time.monotonic_ns()

        async def close(self):
            await self.task
            await super().close()

    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.01,
            "greeting_wait_seconds": 0.1,
            "turns": [{"audio": str(audio_fixture(tmp_path))}],
        }
    )
    peer = DelayedGreetingVAS(replies=(0.01,))
    result = asyncio.run(run_scenario(scenario, peer, tmp_path / "delayed"))
    assert result["status"] == "passed"
    assert peer.inputs[0]["at_ns"] >= peer.greeting_at + 100_000_000


def test_unfinished_greeting_fails_without_sending_input(tmp_path):
    class StuckGreetingVAS(ScriptedVAS):
        async def connect(self, emit):
            session = await super().connect(emit)
            emit(Event("tts_start", {"is_session_output": True}))
            return session

    scenario = Scenario.from_dict(
        {
            "greeting_timeout_seconds": 0.05,
            "turns": [{"audio": str(audio_fixture(tmp_path))}],
        }
    )
    peer = StuckGreetingVAS()
    result = asyncio.run(run_scenario(scenario, peer, tmp_path / "stuck"))
    assert result["status"] == "failed"
    assert "greeting_timeout" in result["error"]
    assert not peer.inputs


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


def test_queued_previous_uplink_frame_never_enters_next_turn(tmp_path):
    class TailVAS(ScriptedVAS):
        async def send_audio(self, path):
            turn = len(self.inputs) + 1
            if turn == 2:
                # A microphone tail already in the queue as the next turn begins.
                self.emit(
                    Event(
                        "input_audio_frame_sent",
                        {
                            "listen_turn_id": 1,
                            "pcm_offset_samples": 17280,
                            "samples": 960,
                        },
                    )
                )
            self.emit(
                Event(
                    "input_audio_frame_sent",
                    {"listen_turn_id": turn, "pcm_offset_samples": 0, "samples": 960},
                )
            )
            return await super().send_audio(path)

    audio = audio_fixture(tmp_path)
    scenario = Scenario.from_dict(
        {
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 1,
            "turns": [{"audio": str(audio)}, {"audio": str(audio)}],
        }
    )
    output = tmp_path / "tail"
    result = asyncio.run(run_scenario(scenario, TailVAS(), output))
    assert result["status"] == "passed"
    for index, turn in enumerate(result["turns"], 1):
        frames = [e for e in turn["events"] if e["event"] == "input_audio_frame_sent"]
        assert [e["data"]["listen_turn_id"] for e in frames] == [index]
        assert [e["data"]["pcm_offset_samples"] for e in frames] == [0]
    # Raw session evidence is retained even if it arrived between turn consumers.
    assert "17280" in (output / "client-events.jsonl").read_text()


def recovery_scenario(tmp_path):
    return Scenario.from_dict(
        {
            "greeting_wait_seconds": 0.01,
            "settle_seconds": 0.01,
            "turn_timeout_seconds": 0.15,
            "turns": [
                {"id": str(i), "audio": str(audio_fixture(tmp_path))} for i in range(3)
            ],
        }
    )


def test_failed_turn_recovers_and_continues_same_session(tmp_path):
    class TimeoutVAS(ScriptedVAS):
        async def send_audio(self, path):
            if not self.inputs:
                self.inputs.append({"path": str(path)})
                return {"frames": 1}
            return await super().send_audio(path)

    peer = TimeoutVAS(replies=(0.01, 0.01, 0.01))
    result = asyncio.run(
        run_scenario(recovery_scenario(tmp_path), peer, tmp_path / "run")
    )
    assert [t["status"] for t in result["turns"]] == [
        "failed",
        "completed",
        "completed",
    ]
    assert result["status"] == "failed"
    assert result["turns"][0]["recovery"]["status"] == "recovered"
    assert len(peer.abort_times) == 1


def test_disconnected_session_marks_remaining_turns_without_reconnecting(tmp_path):
    class DisconnectedVAS(ScriptedVAS):
        async def send_audio(self, path):
            self.inputs.append(path)
            self.emit(Event("disconnected", {"message": "connection lost"}))
            return {}

    peer = DisconnectedVAS()
    result = asyncio.run(
        run_scenario(recovery_scenario(tmp_path), peer, tmp_path / "run")
    )
    assert len(result["turns"]) == 3
    assert all(t["execution_status"] == "not_executed" for t in result["turns"][1:])
    assert len(peer.inputs) == 1
    assert not peer.abort_times


def test_recovery_failure_preserves_report_and_skips_rest(tmp_path):
    class BrokenAbortVAS(ScriptedVAS):
        async def send_audio(self, path):
            self.emit(Event("error", {"message": "bad audio"}))
            return {}

        async def abort(self):
            raise ConnectionError("socket closed")

    result = asyncio.run(
        run_scenario(recovery_scenario(tmp_path), BrokenAbortVAS(), tmp_path / "run")
    )
    assert len(result["turns"]) == 3
    assert result["turns"][0]["error"] == "bad audio"
    assert result["turns"][0]["recovery"]["status"] == "failed"
    assert result["turns"][1]["execution_status"] == "not_executed"


def test_recovery_does_not_accept_stale_or_wrong_turn_stop(tmp_path):
    from voice_scenarios.runner import _recover_turn

    async def exercise():
        events = asyncio.Queue()

        class Peer:
            async def abort(self):
                events.put_nowait(Event("tts_stop", {}, 1))
                events.put_nowait(Event("tts_stop", {"response_listen_turn_id": 2}))

        return await _recover_turn(
            Peer(), events, {"events": [], "listen_turn_id": 1}, 0, timeout=0.03
        )

    result = asyncio.run(exercise())
    assert result["status"] == "failed"
    assert "TimeoutError" in result["error"]


def test_late_audio_is_drained_before_next_turn_and_report_survives(tmp_path):
    from voice_scenarios.report import build_report, evaluate

    class LateAudioVAS(ScriptedVAS):
        async def send_audio(self, path):
            if not self.inputs:
                self.inputs.append(path)
                self.emit(Event("error", {"message": "audio_received_after_tts_stop"}))
                return {}
            return await super().send_audio(path)

        async def abort(self):
            self.emit(Event("tts_stop"))
            self.emit(Event("pcm", {"pcm": b"\x01\x00" * 16000, "sample_rate": 16000}))
            self.emit(Event("tts_stop"))

    result = asyncio.run(
        run_scenario(
            recovery_scenario(tmp_path),
            LateAudioVAS(replies=(0.01,) * 3),
            tmp_path / "run",
        )
    )
    assert [t["status"] for t in result["turns"]] == [
        "failed",
        "completed",
        "completed",
    ]
    assert result["turns"][1]["received_frames"] == 1
    build_report(evaluate(result, []), tmp_path / "report.html")
    assert (tmp_path / "turn-003.html").exists()


def test_unexecuted_turns_have_static_report_pages(tmp_path):
    from voice_scenarios.report import build_report, evaluate

    class Peer(ScriptedVAS):
        async def connect(self, emit):
            raise ConnectionError("offline")

    result = asyncio.run(
        run_scenario(recovery_scenario(tmp_path), Peer(), tmp_path / "run")
    )
    assert len(result["turns"]) == 3
    build_report(evaluate(result, []), tmp_path / "report.html")
    assert "未执行" in (tmp_path / "turn-003.html").read_text()
