"""Read-only evidence comparison for paired DashScope 800/400 ms VAD runs."""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

METRICS = {
    "asr_endpoint_to_final_ms": "same_vas_clock_callback_interval",
    "first_playback_ms": "same_client_clock_playback",
    "input_finish_to_asr_endpoint_ms": "unknown_cross_clock_interval",
    "input_finish_to_asr_endpoint_observed_ms": "upper_bound_including_diagnostic_delivery",
}
INPUT_KEYS = ("mode", "noise_dbfs", "noise_seed", "pre_roll_seconds")


def _load_report(directory):
    report_path = directory / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text())
    else:
        from .report import evaluate

        result = json.loads((directory / "result.json").read_text())
        events_path = directory / "vas-events.jsonl"
        events = (
            [
                json.loads(line)
                for line in events_path.read_text().splitlines()
                if line.strip()
            ]
            if events_path.is_file()
            else []
        )
        report = evaluate(result, events, artifact_dir=directory)
    if not isinstance(report, dict) or not isinstance(report.get("turns"), list):
        raise ValueError("artifact requires an object with a turns list")
    settings_path = directory / "run-settings.json"
    if not report.get("run_metadata") and settings_path.is_file():
        report["run_metadata"] = json.loads(settings_path.read_text())
    return report


def _error(errors, scope, code):
    item = {"scope": scope, "code": code}
    if item not in errors:
        errors.append(item)


def _equal(a, b, key, scope, errors, *, code=None):
    label = code or key
    if a.get(key) in (None, "") or b.get(key) in (None, ""):
        _error(errors, scope, label + "_unknown")
    elif a[key] != b[key]:
        _error(errors, scope, label + "_mismatch")


def _audio_hash(directory, turn, scope, errors):
    filename = turn.get("audio", {}).get("input")
    if not isinstance(filename, str) or not filename:
        _error(errors, scope, "audio_unknown")
        return None
    path = Path(filename)
    path = (directory / (path.name if path.is_absolute() else path)).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        _error(errors, scope, "audio_unknown")
        return None
    with path.open("rb") as audio:
        digest = hashlib.file_digest(audio, "sha256").hexdigest()
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        saved = [
            item.get("sha256")
            for item in manifest.get("inputs", [])
            if item.get("turn_id") == turn.get("id") and not item.get("sensor")
        ]
        if saved and any(value != digest for value in saved):
            _error(errors, scope, "audio_manifest_mismatch")
    return digest


def _asr_settings(turn, threshold, scope, errors):
    groups = {}
    for event in turn.get("vas_events", []):
        name = event.get("event")
        if name not in {"asr_session_config_requested", "asr_session_config_confirmed"}:
            continue
        identity = event.get("span_id"), event.get("clock_id")
        if not all(identity):
            _error(errors, scope, "asr_request_identity_unknown")
            continue
        groups.setdefault(identity, {"requests": [], "confirmations": []})[
            "requests" if name.endswith("requested") else "confirmations"
        ].append(event.get("data", {}))
    if not groups:
        _error(errors, scope, "asr_request_unknown")
    summaries = []
    for (span_id, _), group in groups.items():
        requests, confirmations = group["requests"], group["confirmations"]
        if len(requests) != 1:
            _error(errors, scope, "asr_request_unknown")
        if not confirmations:
            _error(errors, scope, "asr_confirmation_unknown")
        for items, field, label in (
            (requests, "requested_silence_duration_ms", "asr_request"),
            (confirmations, "confirmed_silence_duration_ms", "asr_confirmation"),
        ):
            for data in items:
                value = data.get(field)
                if type(value) is not int:
                    _error(errors, scope, label + "_unknown")
                elif value != threshold:
                    _error(errors, scope, label + "_mismatch")
                if data.get("vad_enabled") is not True:
                    _error(errors, scope, "asr_vad_unconfirmed")
        summaries.append(
            {
                "span_id": span_id,
                "requested_ms": [
                    (
                        r.get("requested_silence_duration_ms")
                        if type(r.get("requested_silence_duration_ms")) is int
                        else None
                    )
                    for r in requests
                ],
                "confirmed_ms": [
                    (
                        c.get("confirmed_silence_duration_ms")
                        if type(c.get("confirmed_silence_duration_ms")) is int
                        else None
                    )
                    for c in confirmations
                ],
            }
        )
    return summaries


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _interval(start, end, key):
    a, b = start.get(key), end.get(key)
    if start.get("clock_id") != end.get("clock_id"):
        return None
    if key == "monotonic_ns" and (
        not start.get("clock_id")
        or not start.get("span_id")
        or start.get("span_id") != end.get("span_id")
    ):
        return None
    if _number(a) and _number(b) and b >= a:
        return (b - a) / 1e6
    return None


def _quality(report):
    categories = {
        name: Counter() for name in ("recognition", "tools", "reply", "audio")
    }
    failures = Counter()
    runtime_failed_turns = 0
    segmentation_review_turns = []
    for turn in report["turns"]:
        if not turn.get("sensor") and (
            sum(e.get("event") == "asr_final" for e in turn.get("vas_events", [])) > 1
            or sum(e.get("event") == "stt" for e in turn.get("events", [])) > 1
        ):
            segmentation_review_turns.append(turn["id"])
        runtime_failed_turns += turn.get("status") == "failed"
        checks = turn.get("checks", [])
        for category, counts in categories.items():
            business = [c for c in checks if c.get("name") == "business." + category]
            if not business:
                counts["not_configured"] += 1
            for check in business:
                counts[check.get("status", "unknown")] += 1
        for check in checks:
            if check.get("passed") is not True:
                failures[check.get("failure_kind") or "unknown"] += 1
    business_failed = sum(counts["failed"] for counts in categories.values())
    business_unknown = sum(
        counts["unknown"] + counts["not_configured"] for counts in categories.values()
    )
    status = (
        "failed"
        if business_failed or failures["functional"] or runtime_failed_turns
        else "unknown" if business_unknown or segmentation_review_turns else "passed"
    )
    return {
        "status": status,
        "business_failed_checks": business_failed,
        "business_unknown_or_unconfigured_checks": business_unknown,
        "runtime_failed_turns": runtime_failed_turns,
        "segmentation_review_turns": segmentation_review_turns,
        "failure_counts": dict(failures),
        "reported_failure_counts": {
            key: len(items) for key, items in report.get("failure_groups", {}).items()
        },
        "categories": {name: dict(counts) for name, counts in categories.items()},
    }


def _measure(turn):
    rows = turn.get("vas_events", [])
    events = turn.get("events", [])
    endpoint = next((e for e in rows if e.get("event") == "asr_endpoint_detected"), {})
    final = next((e for e in rows if e.get("event") == "asr_final"), {})
    speech = next((e for e in events if e.get("event") == "speech_input_finished"), {})
    playback = next((e for e in events if e.get("event") == "playback_started"), {})
    arrival = next(
        (
            e
            for e in events
            if endpoint
            and e.get("event") == "vas_event"
            and all(
                e.get("data", {}).get(key) == endpoint.get(key)
                for key in ("event", "span_id", "clock_id", "monotonic_ns")
            )
        ),
        {},
    )
    return {
        "asr_endpoint_to_final_ms": _interval(endpoint, final, "monotonic_ns"),
        "first_playback_ms": _interval(speech, playback, "at_ns"),
        "input_finish_to_asr_endpoint_ms": None,
        "input_finish_to_asr_endpoint_observed_ms": _interval(speech, arrival, "at_ns"),
    }


def compare_vad_runs(baseline_dir, candidate_dir):
    """Return paired observations, never a claim that a threshold is faster."""
    directories = [Path(baseline_dir).resolve(), Path(candidate_dir).resolve()]
    reports = [_load_report(directory) for directory in directories]
    errors = []
    metadata = [report.get("run_metadata", {}) for report in reports]
    for key in ("device_id", "environment", "endpoint"):
        _equal(
            *metadata, key, "runs", errors, code="device" if key == "device_id" else key
        )
    for label, report in zip(("baseline", "candidate"), reports):
        if report.get("diagnostics", {}).get("complete") is not True:
            _error(errors, label, "diagnostics_incomplete")
    structure = [
        [(t.get("id"), t.get("sensor")) for t in report["turns"]] for report in reports
    ]
    if structure[0] != structure[1] or len(set(structure[0])) != len(structure[0]):
        _error(errors, "runs", "turns_mismatch")
    result = {
        "schema_version": 1,
        "status": "comparable",
        "performance_conclusion": "inconclusive",
        "turns": [],
        "errors": errors,
        "quality": {
            label: _quality(report)
            for label, report in zip(("baseline", "candidate"), reports)
        },
        "excluded_sensor_turns": [
            t["id"] for t in reports[0]["turns"] if t.get("sensor")
        ],
        "limitations": [
            "配对样本只描述观测值，不证明阈值导致性能变化；环境与人设一致性须另行确认。"
        ],
    }
    for baseline, candidate in zip(reports[0]["turns"], reports[1]["turns"]):
        if baseline.get("sensor") or candidate.get("sensor"):
            continue
        scope = baseline["id"]
        hashes = [
            _audio_hash(directory, turn, label + ":" + scope, errors)
            for directory, turn, label in zip(
                directories, (baseline, candidate), ("baseline", "candidate")
            )
        ]
        if all(hashes) and hashes[0] != hashes[1]:
            _error(errors, scope, "audio_mismatch")
        for turn in (baseline, candidate):
            settings = turn.get("input_settings", {})
            if any(key not in settings for key in INPUT_KEYS):
                _error(errors, scope, "input_settings_unknown")
            if (
                not _number(settings.get("noise_dbfs"))
                or not -90 <= settings["noise_dbfs"] <= -10
                or not _number(settings.get("pre_roll_seconds"))
                or settings["pre_roll_seconds"] < 0
                or type(settings.get("noise_seed")) is not int
            ):
                _error(errors, scope, "input_settings_unknown")
            if settings.get("mode") != "vad":
                _error(errors, scope, "vad_mode_required")
        if baseline.get("input_settings") != candidate.get("input_settings"):
            _error(errors, scope, "input_settings_mismatch")
        _equal(baseline, candidate, "playback_source", scope, errors)
        if baseline.get("expected") != candidate.get("expected"):
            _error(errors, scope, "expectations_mismatch")
        if baseline.get("requested_interruption") != candidate.get(
            "requested_interruption"
        ):
            _error(errors, scope, "interruption_mismatch")
        settings = [
            _asr_settings(turn, threshold, label + ":" + scope, errors)
            for turn, threshold, label in zip(
                (baseline, candidate), (800, 400), ("baseline", "candidate")
            )
        ]
        measurements = [_measure(turn) for turn in (baseline, candidate)]
        for label, values in zip(("baseline", "candidate"), measurements):
            for metric in ("asr_endpoint_to_final_ms", "first_playback_ms"):
                if values[metric] is None:
                    _error(errors, label + ":" + scope, metric + "_unknown")
        metrics = {}
        for name, measurement in METRICS.items():
            a, b = (values[name] for values in measurements)
            metrics[name] = {
                "baseline_ms": a,
                "candidate_ms": b,
                "candidate_minus_baseline_ms": (
                    b - a if a is not None and b is not None else None
                ),
                "measurement": measurement,
            }
        result["turns"].append(
            {
                "id": scope,
                "metrics": metrics,
                "baseline_audio_sha256": hashes[0],
                "candidate_audio_sha256": hashes[1],
                "baseline_asr_settings": settings[0],
                "candidate_asr_settings": settings[1],
            }
        )
    if not result["turns"]:
        _error(errors, "runs", "audio_turns_missing")
    result["comparable"] = not errors
    if errors:
        result["status"] = "invalid"
        for turn in result["turns"]:
            for metric in turn["metrics"].values():
                metric["candidate_minus_baseline_ms"] = None
    elif any(
        q["status"] == "failed"
        or q["failure_counts"].get("latency")
        or q["reported_failure_counts"].get("functional")
        or q["reported_failure_counts"].get("latency")
        for q in result["quality"].values()
    ):
        result["status"] = "failed"
    elif any(q["status"] == "unknown" for q in result["quality"].values()):
        result["status"] = "inconclusive"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "baseline", type=Path, help="800 ms result/report artifact directory"
    )
    parser.add_argument(
        "candidate", type=Path, help="400 ms result/report artifact directory"
    )
    parser.add_argument(
        "--output", type=Path, help="write summary JSON here (default: stdout)"
    )
    args = parser.parse_args(argv)
    try:
        result = compare_vad_runs(args.baseline, args.candidate)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {
            "schema_version": 1,
            "status": "invalid",
            "comparable": False,
            "performance_conclusion": "inconclusive",
            "errors": [
                {"code": "artifact_unreadable", "error_type": type(exc).__name__}
            ],
        }
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(payload)
    else:
        sys.stdout.write(payload)
    return (
        0
        if result["status"] == "comparable"
        else 2 if result["status"] == "invalid" else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
