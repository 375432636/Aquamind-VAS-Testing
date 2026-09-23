"""A failed or incomplete batch cannot become green after artifact upload."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_batch_result.py"
spec = importlib.util.spec_from_file_location("batch_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "malformed",
        "empty",
        "pending",
        "failed",
        "archive",
        "report",
        "incomplete",
        "path_escape",
        "wrong_type",
    ],
)
def test_gate_rejects_incomplete_outputs(tmp_path, defect):
    data = dict(
        status="passed",
        archive_status="passed",
        sessions=[
            dict(
                status="passed",
                archive_status="passed",
                report="report.html",
                completed_turns=3,
                turn_count=3,
            )
        ],
    )
    (tmp_path / "report.html").write_text("report")
    if defect == "missing":
        assert not gate.check(tmp_path, archive=True)
        return
    if defect == "empty":
        data["sessions"] = []
    elif defect in ("pending", "failed"):
        data["sessions"][0]["status"] = defect
    elif defect == "archive":
        data["sessions"][0]["archive_status"] = "failed"
    elif defect == "report":
        (tmp_path / "report.html").unlink()
    elif defect == "incomplete":
        data["sessions"][0]["completed_turns"] = 2
    elif defect == "path_escape":
        data["sessions"][0]["report"] = "../report.html"
    elif defect == "wrong_type":
        data["sessions"] = [None]
    (tmp_path / "batch.json").write_text(
        "{" if defect == "malformed" else json.dumps(data)
    )
    assert not gate.check(tmp_path, archive=True)


def test_gate_checks_step_outcomes_even_when_all_sessions_pass(tmp_path):
    data = dict(
        status="passed",
        archive_status="passed",
        sessions=[
            dict(
                status="passed",
                archive_status="passed",
                report="report.html",
                completed_turns=3,
                turn_count=3,
            )
        ],
    )
    (tmp_path / "report.html").write_text("report")
    (tmp_path / "batch.json").write_text(json.dumps(data))
    assert gate.check(tmp_path, archive=True)
    command = [sys.executable, str(SCRIPT), str(tmp_path), str(tmp_path), "--outcome"]
    assert subprocess.run(command + ["success"], capture_output=True).returncode == 0
    for outcome in ("failure", "skipped", "cancelled"):
        assert subprocess.run(command + [outcome], capture_output=True).returncode == 1
