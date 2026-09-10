import asyncio
from pathlib import Path

from voice_scenarios.diagnostics import DiagnosticCollector


def test_replay_dedup_and_gap_cannot_produce_passing_regression(tmp_path):
    collector = DiagnosticCollector(tmp_path)
    collector.bind_session("session")
    row = {
        "server_session_id": "session",
        "seq": 1,
        "event": "capture_started",
        "monotonic_ns": 10,
    }
    collector.accept(
        {
            "schema_version": 1,
            "server_session_id": "session",
            "events": [row],
            "next_seq": 1,
            "gap": False,
            "complete": True,
            "finished": False,
        }
    )
    collector.accept(
        {
            "schema_version": 1,
            "server_session_id": "session",
            "events": [row],
            "next_seq": 1,
            "gap": False,
            "complete": True,
            "finished": False,
        }
    )
    assert len(collector.events) == 1
    collector.accept(
        {
            "schema_version": 1,
            "server_session_id": "session",
            "events": [
                {"server_session_id": "session", "seq": 3, "event": "capture_finished"}
            ],
            "next_seq": 3,
            "gap": True,
            "complete": False,
            "finished": True,
            "end_seq": 3,
        }
    )
    assert not collector.complete and collector.finished

    asyncio.run(collector.finish())


def test_disconnect_without_terminal_batch_is_incomplete(tmp_path):
    collector = DiagnosticCollector(tmp_path)
    collector.bind_session("session")
    collector.accept(
        {
            "schema_version": 1,
            "server_session_id": "session",
            "events": [],
            "next_seq": 0,
            "gap": False,
            "complete": True,
            "finished": False,
        }
    )
    result = asyncio.run(collector.finish())
    assert not result["complete"] and not result["finished"]
    assert "diagnostic_stream_ended_before_final_batch" in result["errors"]
