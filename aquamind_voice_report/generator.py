"""One deep interface for regenerating the portable report from raw evidence."""

import json
from pathlib import Path

from .reply_audio import prepare_reply_audio
from .report import build_report, evaluate
from .report_archive import restore_audio
from .session_chain import build_session_chain
from .session_timing import prepare_session_playback


def generate_report(directory):
    """Generate the canonical JSON, metrics, playback and HTML report in place."""
    directory = Path(directory)
    restore_audio(directory)
    result = json.loads((directory / "result.json").read_text())
    event_path = directory / "vas-events.jsonl"
    events = (
        [json.loads(line) for line in event_path.read_text().splitlines()]
        if event_path.exists()
        else []
    )
    report = evaluate(result, events)
    prepare_reply_audio(report, directory)
    prepare_session_playback(report, directory)
    report["session_chain"] = build_session_chain(report)
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    build_report(report, directory / "report.html")
    metrics = {turn["id"]: turn["metrics"] for turn in report["turns"]}
    (directory / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2)
    )
    return report
