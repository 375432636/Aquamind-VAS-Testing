"""Bounded failure reasons shared by console, Actions summaries and JUnit."""

import json
import os
import re
from pathlib import Path


def failure_reasons(report, *, token=None, limit=5):
    """Keep actionable errors first, folding repeated packet errors by turn."""
    token = os.getenv("VAS_TOKEN") if token is None else token
    reasons = []
    replacements = {}
    if report.get("error"):
        reasons.append(str(report["error"]))
    for turn in report.get("turns", []):
        if turn.get("error"):
            reasons.append(f'{turn["id"]}: {turn["error"]}')
        for check in turn.get("checks", []):
            if check.get("passed"):
                continue
            full = f'{turn["id"]}: {check["name"]} 期望 {check.get("expected")}，实际 {check.get("actual")}'
            # Text assertions may include private conversation contents. The full
            # values already live in the downloadable report, not CI error logs.
            replacements[full] = (
                f'{turn["id"]}: asr_text 断言失败（文本见详细报告）'
                if check["name"] == "asr_text"
                else full
            )
            reasons.append(replacements[full])
    reasons.extend(
        replacements.get(str(item), str(item)) for item in report.get("failures", [])
    )
    grouped = {}
    seen = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        key = re.sub(r"\bseq=\d+\b", "seq=…", reason)
        if key in grouped:
            grouped[key][1] += 1
        else:
            grouped[key] = [reason, 1]
    result = []
    for reason, count in list(grouped.values())[:limit]:
        if token:
            reason = reason.replace(token, "[redacted]")
        # Keep untrusted values on one console line, including control codes.
        reason = re.sub(r"[\x00-\x20\x7f]+", " ", reason).strip()
        if len(reason) > 400:
            reason = reason[:400] + "…"
        result.append(reason + (f"（共 {count} 条）" if count > 1 else ""))
    if len(grouped) > limit:
        result.append(f"另有 {len(grouped) - limit} 类错误，见详细报告")
    if not result and report.get("status") == "failed":
        result.append("运行失败，原因未记录；请查看详细报告和执行日志")
    return result


def read_failure_reasons(directory, *, token=None):
    path = Path(directory) / "report.json"
    if not path.is_file():
        return []
    return failure_reasons(json.loads(path.read_text(encoding="utf-8")), token=token)
