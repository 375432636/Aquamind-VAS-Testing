"""Rendering errors must preserve evidence and never prevent a failure report."""

import asyncio
import json
from xml.etree import ElementTree as ET

import pytest

from voice_scenarios import batch_run, ci_run
from voice_scenarios.session_failure import (
    SessionStageError,
    write_failed_session_report,
)


def test_minimal_report_preserves_partial_result_and_invalid_report(tmp_path):
    original = dict(
        name="partial",
        status="passed",
        turns=[dict(id="first", status="passed")],
        session={"session_id": "same-session"},
    )
    (tmp_path / "result.json").write_text(json.dumps(original))
    (tmp_path / "report.json").write_text("{invalid")
    report = write_failed_session_report(
        tmp_path, "partial", "report", "<render failed>"
    )
    assert report["turns"] == original["turns"]
    assert report["session"] == original["session"]
    assert (tmp_path / "report.invalid.json").read_text() == "{invalid"
    assert "&lt;render failed&gt;" in (tmp_path / "report.html").read_text()


def test_real_execution_boundary_classifies_report_failure(tmp_path, monkeypatch):
    async def run(*args):
        return dict(name="test", status="passed", turns=[])

    def fail(*args):
        raise RuntimeError("render failed")

    monkeypatch.setattr(ci_run, "run_scenario", run)
    monkeypatch.setattr(ci_run, "create_report", fail)
    settings = dict(
        endpoint="wss://example.test",
        device_id="AA:BB:CC:DD:EE:91",
        diagnostics="frame",
    )
    with pytest.raises(SessionStageError) as error:
        asyncio.run(ci_run.execute_prepared(tmp_path, {}, settings, None))
    assert error.value.stage == "report"
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "passed"


@pytest.mark.parametrize("broken", ["excel", "junit"])
def test_aggregation_marks_failures_and_keeps_html_and_manifest(
    tmp_path, monkeypatch, broken
):
    directory = tmp_path / "sessions/one"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(
        json.dumps(dict(name="one", status="passed", turns=[]))
    )
    item = dict(
        id="one",
        name="one",
        source="one.yaml",
        directory="sessions/one",
        status="passed",
    )
    batch = dict(status="passed", sessions=[item])
    if broken == "junit":
        (directory / "junit.xml").write_text("<invalid")
    else:

        def fail(*args):
            raise RuntimeError("xlsx failed")

        monkeypatch.setattr(batch_run, "export_excel", fail)
    batch_run.write_batch(tmp_path, batch)
    assert json.loads((tmp_path / "batch.json").read_text())["status"] == "failed"
    assert "failed" in (tmp_path / "index.html").read_text()
    assert ET.parse(tmp_path / "junit.xml").getroot().get("failures") == "1"
