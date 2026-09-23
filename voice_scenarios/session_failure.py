"""Failure reporting independent of the timeline renderer and audio processing."""

import html
import json
from pathlib import Path


class SessionStageError(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def write_failed_session_report(directory, name, stage, error, metadata=None):
    """Retain raw evidence; always provide a small, standalone failure page."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "result.json"
    result = dict(metadata or {}, name=name, status="failed", turns=[])
    if result_path.exists():
        try:
            result.update(json.loads(result_path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            # Keep invalid raw evidence too, instead of overwriting it silently.
            result_path.replace(directory / "result.invalid.json")
    result.update(status="failed", failure_stage=stage, error=error)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = directory / "report.json"
    report = dict(result)
    if report_path.exists():
        try:
            report.update(json.loads(report_path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            report_path.replace(directory / "report.invalid.json")
    report.update(
        status="failed", failure_stage=stage, error=error, fallback_report=True
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>会话测试失败</title><style>body{font:16px/1.6 system-ui;max-width:850px;margin:48px auto;padding:24px}pre{white-space:pre-wrap}</style>"
        f"<h1>{html.escape(name)}</h1><p>failed · {html.escape(stage)}</p>"
        f'<pre>{html.escape(error)}</pre><p><a href="result.json">原始结果</a> · '
        '<a href="report.json">报告数据</a></p></html>'
    )
    (directory / "report.html").write_text(page, encoding="utf-8")
    return report
