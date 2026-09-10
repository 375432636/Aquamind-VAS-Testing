"""Saved session files exercise the same CLI used by manual Actions runs."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import opuslib_next as opuslib
import pytest
import yaml
from websockets.asyncio.server import serve

from voice_scenarios.batch_run import write_batch

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scenario_folder():
    (ROOT / "scenarios").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="test-batch-", dir=ROOT / "scenarios"
    ) as directory:
        yield Path(directory)


def write_scenario(directory, filename, **overrides):
    data = {"name": filename, "turns": [{"audio": "fixtures/input.wav"}]}
    data.update(overrides)
    path = directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def invoke(scenarios, output, *flags, **settings):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("VAS_") and key != "GITHUB_STEP_SUMMARY"
    }
    env.update(VAS_ENVIRONMENT="dev", VAS_DEVICE_ID="AA:BB:CC:DD:EE:91", **settings)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_scenarios.batch_run",
            "--scenarios",
            str(scenarios),
            "--output",
            str(output),
            *flags,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )


def test_saved_files_preserve_session_settings_and_prepare_before_any_connection(
    scenario_folder, tmp_path
):
    write_scenario(
        scenario_folder, "manual.yaml", input_mode="manual", turn_timeout_seconds=30
    )
    write_scenario(
        scenario_folder,
        "vad.yaml",
        input_mode="vad",
        turn_timeout_seconds=60,
        turns=[
            {
                "audio": "fixtures/input.wav",
                "interrupt_after_seconds": 2,
                "output_kind": "answer",
            },
            {"text": "继续介绍刚才的第一个。"},
        ],
    )
    output = tmp_path / "batch"
    outcome = invoke(scenario_folder, output, "--prepare-only", VAS_INPUT_MODE="manual")
    assert outcome.returncode == 0, outcome.stderr
    batch = json.loads((output / "batch.json").read_text())
    assert batch["status"] == "prepared"
    assert [item["name"] for item in batch["sessions"]] == ["manual.yaml", "vad.yaml"]
    prepared = [
        yaml.safe_load((output / item["directory"] / "scenario.yaml").read_text())
        for item in batch["sessions"]
    ]
    assert [item["input"]["mode"] for item in prepared] == ["manual", "vad"]
    assert [item["turn_timeout_seconds"] for item in prepared] == [30, 60]
    assert prepared[1]["turns"][0]["interrupt"] == {
        "after_playback_seconds": 2,
        "output_kind": "answer",
    }
    assert prepared[1]["turns"][1]["input_text"] == "继续介绍刚才的第一个。"
    assert all(
        not (output / item["directory"] / "result.json").exists()
        for item in batch["sessions"]
    )


@pytest.mark.parametrize("first_fails", [False, True])
def test_batch_opens_one_connection_per_session_and_continues_after_failure(
    scenario_folder, tmp_path, first_fails
):
    for name in ("01-first.yaml", "02-second.yaml"):
        write_scenario(
            scenario_folder,
            name,
            turns=[{"audio": "fixtures/input.wav"}, {"audio": "fixtures/input.wav"}],
        )
    output = tmp_path / "batch"
    connections = []

    async def exercise():
        async def peer(ws):
            session = {"hello": 0, "stop": 0}
            connections.append(session)
            session_id = f"session-{len(connections)}"
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if message["type"] == "hello":
                    session["hello"] += 1
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": session_id,
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )
                elif message["type"] == "listen" and message["state"] == "stop":
                    session["stop"] += 1
                    if first_fails and session_id == "session-1":
                        await ws.close()
                        return
                    await ws.send(json.dumps({"type": "stt", "text": "测试提问"}))
                    await ws.send(json.dumps({"type": "tts", "state": "start"}))
                    await ws.send(
                        json.dumps(
                            {
                                "type": "tts",
                                "state": "sentence_start",
                                "text": "测试回复",
                            }
                        )
                    )
                    await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))

        async with serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await asyncio.to_thread(
                invoke,
                scenario_folder,
                output,
                VAS_DEV_URL=f"ws://127.0.0.1:{port}/looomyn/v1/",
                VAS_DIAGNOSTICS="off",
            )

    outcome = asyncio.run(exercise())
    assert outcome.returncode == (1 if first_fails else 0), outcome.stderr
    if first_fails:
        assert "失败原因 |" in outcome.stderr
        assert "report.html" in outcome.stderr
    batch = json.loads((output / "batch.json").read_text())
    assert [item["status"] for item in batch["sessions"]] == (
        ["failed", "passed"] if first_fails else ["passed", "passed"]
    )
    assert connections == [
        {"hello": 1, "stop": 1 if first_fails else 2},
        {"hello": 1, "stop": 2},
    ]
    reports = [
        json.loads((output / item["directory"] / "report.json").read_text())
        for item in batch["sessions"]
    ]
    assert [item["session"]["session_id"] for item in reports] == [
        "session-1",
        "session-2",
    ]
    assert len(reports[1]["turns"]) == 2
    if first_fails:
        assert reports[0]["turns"][0]["error"] in outcome.stderr
        assert batch["sessions"][0]["failure_reasons"]
    assert all(
        item["report"] in (output / "index.html").read_text()
        for item in batch["sessions"]
    )
    suites = ET.parse(output / "junit.xml").getroot()
    assert len(suites.findall("testsuite")) == 2
    assert int(suites.get("failures")) == (1 if first_fails else 0)
    if not first_fails and os.getenv("CI_BATCH_REPORT_DIR"):
        shutil.copytree(
            output, Path(os.environ["CI_BATCH_REPORT_DIR"]), dirs_exist_ok=True
        )


def test_later_invalid_session_prevents_all_connections_and_speech_generation(
    scenario_folder, tmp_path
):
    write_scenario(scenario_folder, "01-valid.yaml", turns=[{"text": "你好"}])
    write_scenario(
        scenario_folder,
        "02-invalid.yaml",
        turns=[{"text": "你好", "interrupt_after_seconds": -1}],
    )
    output = tmp_path / "invalid"
    outcome = invoke(scenario_folder, output, VAS_DEV_URL="ws://127.0.0.1:1/")
    assert outcome.returncode == 2
    assert "02-invalid.yaml" in outcome.stderr
    assert not list(output.rglob("*.wav"))
    assert not list(output.rglob("result.json"))
    assert json.loads((output / "batch.json").read_text())["status"] == "failed"
    assert ET.parse(output / "junit.xml").getroot().get("failures") == "1"


def test_single_json_session_is_supported_and_existing_runs_are_never_overwritten(
    scenario_folder, tmp_path
):
    path = scenario_folder / "one.json"
    path.write_text(
        json.dumps({"name": "A < B", "turns": [{"audio": "fixtures/input.wav"}]})
    )
    output = tmp_path / "run"
    outcome = invoke(path, output, "--prepare-only")
    assert outcome.returncode == 0, outcome.stderr
    before = (output / "batch.json").read_bytes()
    assert "A &lt; B" in (output / "report.html").read_text()
    outcome = invoke(path, output, "--prepare-only")
    assert outcome.returncode == 2
    assert (output / "batch.json").read_bytes() == before


@pytest.mark.parametrize(
    "path", ["README.md", "scenarios/../README.md", "scenarios/missing.yaml"]
)
def test_batch_rejects_paths_outside_saved_scenarios_or_missing_files(tmp_path, path):
    outcome = invoke(path, tmp_path / "invalid", "--prepare-only")
    assert outcome.returncode == 2
    assert not list((tmp_path / "invalid").rglob("*.wav"))


def test_same_slug_paths_get_distinct_stable_session_directories(
    scenario_folder, tmp_path
):
    write_scenario(scenario_folder, "a/b.yaml")
    write_scenario(scenario_folder, "a-b.yaml")
    first, second = tmp_path / "first", tmp_path / "second"
    for output in (first, second):
        outcome = invoke(scenario_folder, output, "--prepare-only")
        assert outcome.returncode == 0, outcome.stderr
    one = json.loads((first / "batch.json").read_text())
    two = json.loads((second / "batch.json").read_text())
    ids = [item["id"] for item in one["sessions"]]
    assert len(set(ids)) == 2
    assert ids == [item["id"] for item in two["sessions"]]


@pytest.mark.parametrize("destination", ["stderr", "github"])
def test_batch_summary_is_available_for_actions_even_when_validation_fails(
    scenario_folder, tmp_path, monkeypatch, destination
):
    inherited = tmp_path / "inherited-actions-summary.md"
    inherited.write_text("Existing Actions summary\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(inherited))
    write_scenario(scenario_folder, "bad.yaml", name="Unexpected", turns=[])
    output = tmp_path / "invalid"
    invoke(scenario_folder, output, "--prepare-only")
    selected = tmp_path / "selected-actions-summary.md"
    selected.write_text("Previous test step\n", encoding="utf-8")
    settings = {"GITHUB_STEP_SUMMARY": str(selected)} if destination == "github" else {}
    summary = invoke(
        scenario_folder, tmp_path / "unused", "--summarize", str(output), **settings
    )
    assert summary.returncode == 0
    content = (
        selected.read_text(encoding="utf-8")
        if destination == "github"
        else summary.stderr
    )
    assert "VAS 批量测试" in content
    assert "failed" in content
    assert "bad" in content
    assert inherited.read_text(encoding="utf-8") == "Existing Actions summary\n"
    if destination == "github":
        assert content.startswith("Previous test step\n")
        assert not summary.stderr
    else:
        assert selected.read_text(encoding="utf-8") == "Previous test step\n"


def test_aggregate_junit_records_session_failure_when_individual_report_is_missing(
    tmp_path,
):
    write_batch(
        tmp_path,
        {
            "status": "failed",
            "sessions": [
                {
                    "id": "broken",
                    "source": "scenarios/broken.yaml",
                    "name": "Report failure",
                    "status": "failed",
                    "directory": "sessions/broken",
                    "error": "OSError: cannot write session report",
                }
            ],
        },
    )
    suites = ET.parse(tmp_path / "junit.xml").getroot()
    assert suites.get("tests") == "1"
    assert suites.get("failures") == "1"
    assert (
        suites.find(".//failure").get("message")
        == "OSError: cannot write session report"
    )


def test_batch_summary_reads_session_failure_reasons_from_existing_artifacts(tmp_path):
    from voice_scenarios.batch_run import summarize

    directory = tmp_path / "sessions/broken"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "turns": [],
                "failures": [
                    f"second: 播放了属于其他轮次的迟到音频 seq={number}"
                    for number in range(100, 300)
                ],
            }
        )
    )
    write_batch(
        tmp_path,
        {
            "status": "failed",
            "sessions": [
                {
                    "id": "broken",
                    "source": "scenarios/broken.yaml",
                    "name": "Failed session",
                    "status": "failed",
                    "directory": "sessions/broken",
                    "report": "sessions/broken/report.html",
                }
            ],
        },
    )
    summary = summarize(tmp_path)
    assert "迟到音频" in summary
    assert "200 条" in summary
    assert "sessions/broken/report.html" in summary
