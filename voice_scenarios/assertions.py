"""Optional business checks, keeping missing evidence separate from wrong behavior."""

import math
import re
import unicodedata
import wave
from collections import Counter
from pathlib import Path

from .reply_timing import analyze_reply_timing
from .robot_output_monitor import evaluate_robot_output, validate_robot_output

CATEGORIES = ("recognition", "tools", "reply", "audio", "robot_output")
_COMMON = {"verified", "reason", "source"}


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 1000


def _groups(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(group, list) and bool(group) and all(_text(v) for v in group)
            for group in value
        )
    )


def validate_business_assertions(config):
    """Reject invalid assertions before any speech synthesis or VAS connection."""
    if not isinstance(config, dict) or not config or set(config) - set(CATEGORIES):
        raise ValueError(
            "business requires recognition, tools, reply, audio or robot_output objects"
        )
    fields = {
        "recognition": {"contains_all", "sensor"},
        "tools": {"required", "forbidden"},
        "reply": {"contains_all", "output_kind"},
        "audio": {"ending", "min_duration_ms"},
        "robot_output": {
            "monitor",
            "called",
            "action",
            "product_refs",
            "expression",
            "navigation",
        },
    }
    for category, spec in config.items():
        if not isinstance(spec, dict) or set(spec) - fields[category] - _COMMON:
            raise ValueError(f"business.{category} contains unsupported fields")
        if "verified" in spec and type(spec["verified"]) is not bool:
            raise ValueError("business verified must be a boolean")
        if any(key in spec and not _text(spec[key]) for key in ("source", "reason")):
            raise ValueError("business source/reason must be nonempty text")
        if spec.get("verified") is False:
            if not _text(spec.get("reason")):
                raise ValueError("unverified business expectations require a reason")
            # An unknown device contract may have no proposed expectations yet.
            if not (set(spec) - _COMMON):
                continue
        if category == "robot_output":
            validate_robot_output(
                {key: value for key, value in spec.items() if key not in _COMMON}
            )
            continue
        if category in {"recognition", "reply"}:
            if "contains_all" in spec and not _groups(spec["contains_all"]):
                raise ValueError("contains_all must contain nonempty synonym groups")
            if category == "recognition" and "sensor" in spec:
                if (
                    spec["sensor"]
                    not in {"touch-head", "touch-hand", "shake-body", "throw-it-up"}
                    or "contains_all" in spec
                ):
                    raise ValueError("recognition requires sensor or contains_all")
            elif "contains_all" not in spec:
                raise ValueError(f"business.{category} requires contains_all")
            if category == "reply" and spec.get("output_kind", "answer") not in {
                "answer",
                "any",
            }:
                raise ValueError("reply output_kind must be answer or any")
        elif category == "tools":
            if not {"required", "forbidden"} & set(spec):
                raise ValueError("business.tools requires required or forbidden")
            if "forbidden" in spec and (
                not isinstance(spec["forbidden"], list)
                or not all(_text(name) for name in spec["forbidden"])
            ):
                raise ValueError("forbidden tools must be a list of names")
            required = spec.get("required", [])
            if not isinstance(required, list):
                raise ValueError("required tools must be a list")
            for item in required:
                if (
                    not isinstance(item, dict)
                    or set(item) - {"name", "arguments"}
                    or not _text(item.get("name"))
                ):
                    raise ValueError(
                        "required tool requires name and optional arguments"
                    )
                args = item.get("arguments", {})
                if not isinstance(args, dict) or any(
                    not _text(key)
                    or type(value) not in {str, int, float, bool, type(None)}
                    or (type(value) is float and not math.isfinite(value))
                    for key, value in args.items()
                ):
                    raise ValueError(
                        "tool arguments must map keys to finite JSON scalar values"
                    )
            if {item["name"] for item in required} & set(spec.get("forbidden", [])):
                raise ValueError("a tool cannot be both required and forbidden")
        else:
            if spec.get("ending", "normal") not in {"normal", "interrupted"}:
                raise ValueError("audio ending must be normal or interrupted")
            duration = spec.get("min_duration_ms", 1)
            if (
                type(duration) not in {int, float}
                or not math.isfinite(duration)
                or duration <= 0
            ):
                raise ValueError("audio min_duration_ms must be finite and positive")


def _check(category, spec, actual, status, reason, failure_kind=None):
    return {
        "name": f"business.{category}",
        "category": category,
        "status": status,
        "failure_kind": failure_kind,
        "passed": status in {"passed", "not_applicable"},
        "expected": spec,
        "actual": actual,
        "reason": reason,
    }


def _normalized(text):
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _semantic(category, spec, actual):
    text = _normalized(actual["text"])
    missing = [
        group
        for group in spec["contains_all"]
        if not any(_normalized(word) in text for word in group)
    ]
    actual["missing_groups"] = missing
    return _check(
        category,
        spec,
        actual,
        "failed" if missing else "passed",
        (
            "缺少必要语义组：" + "；".join(" / ".join(group) for group in missing)
            if missing
            else "各必要语义组均命中至少一个表达"
        ),
        "functional" if missing else None,
    )


def _belongs(turn, data):
    return not data.get("is_session_output") and data.get(
        "response_listen_turn_id"
    ) in (None, turn.get("listen_turn_id"))


def _recognition(turn, spec):
    if "sensor" in spec:
        sent = [
            e.get("data", {}).get("mode")
            for e in turn.get("events", [])
            if e["event"] == "sensor_sent"
        ]
        echo = re.search(
            r"""<detected_action\b[^>]*\bsensor_name=["']([^"']+)["']""",
            turn.get("server_input_text") or "",
        )
        actual = {
            "source": "sensor",
            "command": sent[-1] if sent else None,
            "server_command": echo.group(1) if echo else None,
        }
        matched = (
            turn.get("sensor") == spec["sensor"]
            and actual["command"] == spec["sensor"]
            and actual["server_command"] in {None, spec["sensor"]}
        )
        if not sent and turn.get("sensor") == spec["sensor"]:
            return _check(
                "recognition",
                spec,
                actual,
                "unknown",
                "缺少传感器实际发送记录；不能仅凭场景配置认定已发送",
                "diagnostic_missing",
            )
        return _check(
            "recognition",
            spec,
            actual,
            "not_applicable" if matched else "failed",
            "传感器指令已匹配；本轮不经过 ASR" if matched else "传感器指令与预期不符",
            None if matched else "functional",
        )
    if turn.get("sensor"):
        return _check(
            "recognition",
            spec,
            {"source": "sensor"},
            "unknown",
            "传感器轮次没有 ASR 文本，请配置 recognition.sensor",
            "configuration_unverified",
        )
    texts = [
        e.get("data", {}).get("text")
        for e in turn.get("events", [])
        if e["event"] == "stt" and _belongs(turn, e.get("data", {}))
    ]
    text = texts[-1] if texts else None
    if not isinstance(text, str):
        return _check(
            "recognition",
            spec,
            {"text": None},
            "unknown",
            "未收到本轮识别文本",
            "diagnostic_missing",
        )
    return _semantic(
        "recognition", spec, {"text": text, "source": "stt", "final_count": len(texts)}
    )


def _tools(rows, spec, diagnostics_complete):
    calls = [e for e in rows if e["event"] == "tool_call_started"]
    finishes = {
        e.get("span_id"): e
        for e in rows
        if e["event"] == "tool_call_finished" and e.get("span_id")
    }
    actual = {
        "calls": dict(Counter(e.get("data", {}).get("tool_name") for e in calls)),
        "arguments": [],
    }
    if not spec.get("required") and not spec.get("forbidden"):
        return _check(
            "tools",
            spec,
            actual,
            "not_applicable",
            "此场景没有规定必须或禁止的工具；仍保留实际调用供核对",
        )
    failures, missing = [], []
    for name in spec.get("forbidden", []):
        if actual["calls"].get(name):
            failures.append(f"调用了禁止工具 {name}")
    for required in spec.get("required", []):
        name, wanted = required["name"], required.get("arguments", {})
        candidates = [e for e in calls if e.get("data", {}).get("tool_name") == name]
        if not candidates:
            (failures if diagnostics_complete is True else missing).append(
                f"未观察到必须工具 {name}"
            )
            continue
        matched, unknown, reasons = False, False, []
        for call in candidates:
            args = call.get("data", {}).get("arguments")
            observed = {
                key: args[key]
                for key in wanted
                if isinstance(args, dict) and key in args
            }
            if wanted:
                actual["arguments"].append(
                    {
                        "name": name,
                        "values": observed,
                        "missing_keys": sorted(set(wanted) - set(observed)),
                    }
                )
                if any(
                    observed[key] != wanted[key]
                    or type(observed[key]) is not type(wanted[key])
                    for key in observed
                ):
                    reasons.append(f"工具 {name} 的关键参数不符合预期")
                    continue
                if len(observed) != len(wanted):
                    unknown = True
                    continue
            end = finishes.get(call.get("span_id"))
            if end is None or end.get("status") is None:
                unknown = True
            elif end["status"] not in {"ok", "success"}:
                reasons.append(f"工具 {name} 执行未成功（{end['status']}）")
            else:
                matched = True
        if not matched:
            if unknown:
                missing.append(f"工具 {name} 的参数或执行结果未采集完整")
            else:
                failures.extend(reasons)
    if spec.get("forbidden") and diagnostics_complete is not True:
        missing.append("诊断不完整，无法证明禁止工具未被调用")
    if failures:
        return _check(
            "tools", spec, actual, "failed", "；".join(failures + missing), "functional"
        )
    if missing:
        return _check(
            "tools", spec, actual, "unknown", "；".join(missing), "diagnostic_missing"
        )
    return _check(
        "tools", spec, actual, "passed", "必须工具、关键参数及禁止工具检查通过"
    )


def _reply(turn, spec):
    events = [e for e in turn.get("events", []) if _belongs(turn, e.get("data", {}))]
    sentences = analyze_reply_timing({**turn, "events": events})["sentences"]
    kind = spec.get("output_kind", "answer")
    selected = [s for s in sentences if s["kind"] == "answer" or kind == "any"]
    if not selected and any(s["kind"] in {"unknown", "mixed"} for s in sentences):
        return _check(
            "reply",
            spec,
            {"text": None},
            "unknown",
            "缺少可靠的正式回复归属，无法排除临时播报",
            "diagnostic_missing",
        )
    if not sentences:
        return _check(
            "reply",
            spec,
            {"text": None},
            "unknown",
            "未采集到本轮回复文本",
            "diagnostic_missing",
        )
    actual = {
        "text": "".join(s["text"] for s in selected),
        "source": "tts_sentence_start",
        "output_kind": kind,
    }
    return _semantic("reply", spec, actual)


def _audio(turn, spec, artifact_dir):
    actual = {"decodable": None, "duration_ms": None, "ending": None}
    packets = [
        e.get("data", {})
        for e in turn.get("events", [])
        if e["event"] == "audio_received"
    ]
    owned = [
        p for p in packets if _belongs(turn, p) and not p.get("discarded_after_abort")
    ]
    if not owned or not any(p.get("bytes", 0) > 0 for p in owned):
        return _check(
            "audio",
            spec,
            actual,
            "failed",
            "没有本轮可用回复音频；开场白及其他轮次音频不计入",
            "functional",
        )
    filename = turn.get("audio", {}).get("received")
    path = Path(filename) if filename else None
    if path is not None and artifact_dir is not None:
        adjacent = Path(artifact_dir) / path.name
        if adjacent.is_file():
            path = adjacent
    if path is None or not path.is_file():
        return _check(
            "audio",
            spec,
            actual,
            "unknown",
            "回复音频文件缺失，无法验证解码",
            "diagnostic_missing",
        )
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (
                1,
                2,
                "NONE",
            ):
                raise ValueError("unsupported_pcm_wav")
            expected_bytes = audio.getnframes() * 2
            decoded_bytes = 0
            while chunk := audio.readframes(8192):
                decoded_bytes += len(chunk)
            if not expected_bytes or expected_bytes != decoded_bytes:
                raise ValueError("empty_or_truncated_wav")
            if decoded_bytes != sum(p.get("bytes", 0) for p in packets):
                raise ValueError("audio_packet_wav_mismatch")
            if any(
                p.get("sample_rate", audio.getframerate()) != audio.getframerate()
                for p in packets
            ):
                raise ValueError("audio_sample_rate_mismatch")
            actual.update(
                decodable=True,
                duration_ms=sum(p.get("bytes", 0) for p in owned)
                * 1000
                / (2 * audio.getframerate()),
            )
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        actual["decodable"] = False
        return _check(
            "audio",
            spec,
            actual,
            "failed",
            f"回复音频不可解码、为空或不完整（{type(exc).__name__}）",
            "functional",
        )
    if actual["duration_ms"] < spec.get("min_duration_ms", 1):
        return _check(
            "audio",
            spec,
            actual,
            "failed",
            "本轮有效回复音频短于配置的最小时长",
            "functional",
        )
    interruption = turn.get("interruption") or {}
    expected_ending = spec.get("ending", "normal")
    stopped = any(
        e["event"] == "tts_stop" and _belongs(turn, e.get("data", {}))
        for e in turn.get("events", [])
    )
    drained = any(e["event"] == "playback_drained" for e in turn.get("events", []))
    normal = (
        turn.get("status") == "completed"
        and stopped
        and drained
        and not interruption.get("abort_requested_at_ns")
    )
    interrupted = (
        turn.get("status") == "interrupted"
        and bool(turn.get("requested_interruption"))
        and interruption.get("abort_requested_at_ns") is not None
        and interruption.get("client_playback_stopped") is True
        and interruption.get("server_stop_observed") is True
    )
    actual["ending"] = (
        "normal" if normal else "interrupted" if interrupted else "incomplete"
    )
    passed = actual["ending"] == expected_ending
    return _check(
        "audio",
        spec,
        actual,
        "passed" if passed else "failed",
        (
            "已解码非空音频，结束方式符合预期"
            if passed
            else f"回复结束方式不符：期望 {expected_ending}，实际 {actual['ending']}"
        ),
        None if passed else "functional",
    )


def evaluate_business_assertions(
    turn, rows, *, diagnostics_complete=None, artifact_dir=None
):
    """Return flat check records; callers keep functional/diagnostic/latency verdicts separate."""
    config = turn.get("expected", {}).get("business")
    if config is None:
        return []
    validate_business_assertions(config)
    checks = []
    for category in CATEGORIES:
        if category not in config:
            continue
        spec = config[category]
        if spec.get("verified") is False:
            check = _check(
                category,
                spec,
                {},
                "unknown",
                spec["reason"],
                "configuration_unverified",
            )
        elif category == "recognition":
            check = _recognition(turn, spec)
        elif category == "tools":
            check = _tools(rows, spec, diagnostics_complete)
        elif category == "reply":
            check = _reply(turn, spec)
        elif category == "robot_output":
            check = evaluate_robot_output(
                turn,
                rows,
                {key: value for key, value in spec.items() if key not in _COMMON},
                diagnostics_complete=diagnostics_complete,
            )
        else:
            check = _audio(turn, spec, artifact_dir)
        checks.append(check)
    return checks
