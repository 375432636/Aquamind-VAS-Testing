import copy
import json
import subprocess
import sys

import pytest

from voice_scenarios.vad_comparison import compare_vad_runs


def artifact(tmp_path, name, threshold, *, playback_ms=1000):
    directory = tmp_path / name
    directory.mkdir()
    audio = directory / "turn-001.input.wav"
    audio.write_bytes(b"identical source audio")

    def server(event, at, data=None):
        return dict(
            event=event,
            monotonic_ns=at,
            clock_id="vas",
            span_id="asr-1",
            listen_turn_id=1,
            data=data or {},
        )

    endpoint = server("asr_endpoint_detected", 100_000_000_000)
    rows = [
        server(
            "asr_session_config_requested",
            99_000_000_000,
            {"vad_enabled": True, "requested_silence_duration_ms": threshold},
        ),
        server(
            "asr_session_config_confirmed",
            99_100_000_000,
            {"vad_enabled": True, "confirmed_silence_duration_ms": threshold},
        ),
        endpoint,
        server("asr_final", 100_100_000_000),
    ]
    checks = [
        dict(
            name=f"business.{category}",
            category=category,
            status="passed",
            passed=True,
            failure_kind=None,
            reason="",
        )
        for category in ("recognition", "tools", "reply", "audio")
    ]
    report = dict(
        status="passed",
        diagnostics={"complete": True},
        run_metadata={
            "device_id": "AA:BB",
            "environment": "dev",
            "endpoint": "ws://127.0.0.1:19080",
        },
        turns=[
            dict(
                id="news",
                status="completed",
                audio={"input": str(audio)},
                input_settings={
                    "mode": "vad",
                    "pre_roll_seconds": 0.3,
                    "noise_dbfs": -55,
                    "noise_seed": 0,
                },
                playback_source="simulated_player",
                requested_interruption=None,
                expected={
                    "business": {
                        category: {"verified": True}
                        for category in ("recognition", "tools", "reply", "audio")
                    }
                },
                checks=checks,
                vas_events=rows,
                asr_settings=rows[:2],
                events=[
                    dict(event="speech_input_finished", at_ns=1_000_000_000, data={}),
                    dict(event="vas_event", at_ns=1_500_000_000, data=endpoint),
                    dict(
                        event="playback_started",
                        at_ns=1_000_000_000 + playback_ms * 1_000_000,
                        data={},
                    ),
                ],
            )
        ],
    )
    (directory / "report.json").write_text(json.dumps(report))
    return directory, report


def save(directory, report):
    (directory / "report.json").write_text(json.dumps(report))


def test_paired_evidence_uses_audio_hashes_and_keeps_exact_endpoint_delay_unknown(
    tmp_path,
):
    baseline, _ = artifact(tmp_path, "800", 800, playback_ms=1000)
    candidate, _ = artifact(tmp_path, "400", 400, playback_ms=700)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "comparable"
    assert result["performance_conclusion"] == "inconclusive"
    pair = result["turns"][0]
    assert pair["metrics"]["first_playback_ms"]["candidate_minus_baseline_ms"] == -300
    assert pair["metrics"]["asr_endpoint_to_final_ms"]["baseline_ms"] == 100
    assert pair["metrics"]["input_finish_to_asr_endpoint_ms"]["baseline_ms"] is None
    observed = pair["metrics"]["input_finish_to_asr_endpoint_observed_ms"]
    assert observed["baseline_ms"] == 500
    assert observed["measurement"] == "upper_bound_including_diagnostic_delivery"


@pytest.mark.parametrize(
    "change,code",
    [
        ("device", "device_mismatch"),
        ("missing_device", "device_unknown"),
        ("environment", "environment_mismatch"),
        ("endpoint", "endpoint_mismatch"),
        ("audio", "audio_mismatch"),
        ("missing_audio", "audio_unknown"),
        ("noise", "input_settings_mismatch"),
        ("missing_noise", "input_settings_unknown"),
        ("manual", "vad_mode_required"),
        ("playback", "playback_source_mismatch"),
        ("expectations", "expectations_mismatch"),
        ("order", "turns_mismatch"),
        ("missing_confirmation", "asr_confirmation_unknown"),
        ("wrong_confirmation", "asr_confirmation_mismatch"),
        ("wrong_request", "asr_request_mismatch"),
        ("wrong_span", "asr_confirmation_unknown"),
        ("missing_span", "asr_request_identity_unknown"),
        ("vad_disabled", "asr_vad_unconfirmed"),
        ("incomplete", "diagnostics_incomplete"),
    ],
)
def test_nonmatching_or_unconfirmed_evidence_cannot_pass(tmp_path, change, code):
    baseline, _ = artifact(tmp_path, "800", 800)
    candidate, report = artifact(tmp_path, "400", 400)
    turn = report["turns"][0]
    rows = turn["vas_events"]
    if change == "device":
        report["run_metadata"]["device_id"] = "different"
    elif change == "missing_device":
        report["run_metadata"].pop("device_id")
    elif change in {"environment", "endpoint"}:
        report["run_metadata"][change] = "different"
    elif change == "audio":
        (candidate / "turn-001.input.wav").write_bytes(b"different")
    elif change == "missing_audio":
        (candidate / "turn-001.input.wav").unlink()
    elif change == "noise":
        turn["input_settings"]["noise_seed"] = 1
    elif change == "missing_noise":
        turn["input_settings"].pop("noise_dbfs")
    elif change == "manual":
        turn["input_settings"]["mode"] = "manual"
    elif change == "playback":
        turn["playback_source"] = "real_speaker"
    elif change == "expectations":
        turn["expected"]["business"]["tools"]["verified"] = False
    elif change == "order":
        turn["id"] = "changed"
    elif change == "missing_confirmation":
        rows.pop(1)
    elif change == "wrong_confirmation":
        rows[1]["data"]["confirmed_silence_duration_ms"] = 800
    elif change == "wrong_request":
        rows[0]["data"]["requested_silence_duration_ms"] = 800
    elif change == "wrong_span":
        rows[1]["span_id"] = "unrelated"
    elif change == "missing_span":
        rows[0]["span_id"] = None
    elif change == "vad_disabled":
        rows[1]["data"]["vad_enabled"] = False
    elif change == "incomplete":
        report["diagnostics"]["complete"] = False
    save(candidate, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "invalid"
    assert result["comparable"] is False
    assert code in {error["code"] for error in result["errors"]}


def test_business_failure_blocks_a_successful_comparison_even_when_latency_falls(
    tmp_path,
):
    baseline, _ = artifact(tmp_path, "800", 800, playback_ms=1000)
    candidate, report = artifact(tmp_path, "400", 400, playback_ms=600)
    report["turns"][0]["checks"][0].update(
        status="failed",
        passed=False,
        failure_kind="functional",
        reason="新闻被识别成心",
    )
    report["turns"][0]["checks"][1].update(
        status="failed", passed=False, failure_kind="functional", reason="调用错误工具"
    )
    save(candidate, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["comparable"] is True
    assert result["status"] == "failed"
    assert result["quality"]["candidate"]["business_failed_checks"] == 2
    assert result["quality"]["candidate"]["categories"]["recognition"]["failed"] == 1


def test_missing_business_checks_are_unknown_not_zero_failures_proving_quality(
    tmp_path,
):
    baseline, _ = artifact(tmp_path, "800", 800)
    candidate, report = artifact(tmp_path, "400", 400)
    report["turns"][0]["checks"] = []
    save(candidate, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "inconclusive"
    assert result["quality"]["candidate"]["status"] == "unknown"


@pytest.mark.parametrize(
    "mismatch", ["server_clock", "server_span", "client_clock", "missing_playback"]
)
def test_unproven_clock_or_request_boundaries_do_not_produce_intervals(
    tmp_path, mismatch
):
    baseline, _ = artifact(tmp_path, "800", 800)
    candidate, report = artifact(tmp_path, "400", 400)
    turn = report["turns"][0]
    if mismatch == "server_clock":
        turn["vas_events"][-1]["clock_id"] = "different-clock"
    elif mismatch == "server_span":
        turn["vas_events"][-1]["span_id"] = "different-request"
    elif mismatch == "client_clock":
        turn["events"][0]["clock_id"] = "one"
        turn["events"][-1]["clock_id"] = "two"
    else:
        turn["events"].pop()
    save(candidate, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "invalid"
    metric = (
        "asr_endpoint_to_final_ms"
        if mismatch.startswith("server")
        else "first_playback_ms"
    )
    assert result["turns"][0]["metrics"][metric]["candidate_ms"] is None


def test_sensor_turns_do_not_require_audio_hashes_or_asr_configuration(tmp_path):
    baseline, a = artifact(tmp_path, "800", 800)
    candidate, b = artifact(tmp_path, "400", 400)
    sensor = {
        "id": "touch",
        "sensor": "touch-head",
        "status": "completed",
        "checks": copy.deepcopy(a["turns"][0]["checks"]),
    }
    for directory, report in ((baseline, a), (candidate, b)):
        report["turns"].append(sensor)
        save(directory, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "comparable"
    assert len(result["turns"]) == 1
    assert result["excluded_sensor_turns"] == ["touch"]


def test_result_only_directories_are_evaluated_without_writing_reports(tmp_path):
    directories = []
    for name, threshold in (("800", 800), ("400", 400)):
        directory, report = artifact(tmp_path, name, threshold)
        turn = report["turns"][0]
        turn["expected"] = {}
        events = turn.pop("vas_events")
        turn.pop("checks")
        (directory / "report.json").unlink()
        (directory / "result.json").write_text(json.dumps(report))
        (directory / "vas-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events)
        )
        directories.append(directory)
    result = compare_vad_runs(*directories)
    assert result["comparable"]
    assert result["status"] == "inconclusive"
    assert all(not (directory / "report.json").exists() for directory in directories)


def test_manifest_disagreement_cannot_hide_changed_audio(tmp_path):
    baseline, _ = artifact(tmp_path, "800", 800)
    candidate, _ = artifact(tmp_path, "400", 400)
    (candidate / "manifest.json").write_text(
        json.dumps({"inputs": [{"turn_id": "news", "sha256": "0" * 64}]})
    )
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "invalid"
    assert "audio_manifest_mismatch" in {error["code"] for error in result["errors"]}


def test_module_cli_writes_only_requested_summary_and_returns_evidence_status(tmp_path):
    baseline, _ = artifact(tmp_path, "800", 800)
    candidate, _ = artifact(tmp_path, "400", 400)
    output = tmp_path / "comparison.json"
    invocation = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_scenarios.vad_comparison",
            str(baseline),
            str(candidate),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert invocation.returncode == 0, invocation.stderr
    assert json.loads(output.read_text())["status"] == "comparable"
    assert not (baseline / "comparison.json").exists()


def test_equal_but_unknown_noise_does_not_establish_same_test_conditions(tmp_path):
    baseline, a = artifact(tmp_path, "800", 800)
    candidate, b = artifact(tmp_path, "400", 400)
    for directory, report in ((baseline, a), (candidate, b)):
        report["turns"][0]["input_settings"]["noise_dbfs"] = None
        save(directory, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "invalid"
    assert "input_settings_unknown" in {error["code"] for error in result["errors"]}


def test_repeated_asr_finals_require_quality_review_even_if_existing_checks_pass(
    tmp_path,
):
    baseline, _ = artifact(tmp_path, "800", 800)
    candidate, report = artifact(tmp_path, "400", 400)
    report["turns"][0]["vas_events"].append(
        copy.deepcopy(report["turns"][0]["vas_events"][-1])
    )
    save(candidate, report)
    result = compare_vad_runs(baseline, candidate)
    assert result["status"] == "inconclusive"
    assert result["quality"]["candidate"]["segmentation_review_turns"] == ["news"]


def test_observed_endpoint_matches_event_identity_after_report_annotation(tmp_path):
    baseline, report = artifact(tmp_path, "800", 800)
    candidate, _ = artifact(tmp_path, "400", 400)
    report = json.loads((baseline / "report.json").read_text())
    report["turns"][0]["vas_events"][2]["client_turn_index"] = 1
    save(baseline, report)
    result = compare_vad_runs(baseline, candidate)
    assert (
        result["turns"][0]["metrics"]["input_finish_to_asr_endpoint_observed_ms"][
            "baseline_ms"
        ]
        == 500
    )
