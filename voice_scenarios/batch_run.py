"""Run saved conversation sessions sequentially and export one batch report."""

import argparse
import asyncio
import hashlib
import html
import json
import logging
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from .ci_run import (
    ROOT,
    execute_prepared,
    prepare,
    settings_from_env,
    validate_audio,
    validate_turns,
)
from .evaluation import validate_evaluation
from .excel_report import export_excel
from .failure_summary import failure_reasons, read_failure_reasons
from .session_failure import write_failed_session_report


def load_sessions(source, env):
    """Validate every saved file and referenced audio before making a connection."""
    root = (ROOT / "scenarios").resolve()
    source = (ROOT / source).resolve()
    if not source.is_relative_to(root):
        raise ValueError(
            "Scenario path must stay inside the repository scenarios/ folder"
        )
    files = sorted(source.rglob("*")) if source.is_dir() else [source]
    files = [
        path for path in files if path.suffix.lower() in {".yaml", ".yml", ".json"}
    ]
    if not 1 <= len(files) <= 100:
        raise ValueError("Select 1–100 JSON/YAML session files inside scenarios/")
    sessions = []
    for path in files:
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("Scenario file must exist inside scenarios/")
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            data = (
                json.loads(text)
                if path.suffix.lower() == ".json"
                else yaml.safe_load(text)
            )
            if not isinstance(data, dict) or set(data) - {
                "name",
                "device_id",
                "environment",
                "input_mode",
                "turn_timeout_seconds",
                "turns",
                "evaluation",
            }:
                raise ValueError(
                    "session supports only name, device_id, environment, input_mode, turn_timeout_seconds, evaluation and turns"
                )
            name = data.get("name", path.stem)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
                raise ValueError("name must contain 1–160 characters")
            device_id = data.get("device_id")
            if not isinstance(device_id, str) or not re.fullmatch(
                r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", device_id
            ):
                raise ValueError(
                    "device_id is required and must be a MAC address such as FF:FF:FF:FF:FF:11"
                )
            environment = data.get("environment")
            if environment not in ("dev", "main"):
                raise ValueError("environment is required and must be dev or main")
            session_env = dict(env)
            session_env.pop("VAS_EVALUATION_JSON", None)
            if "evaluation" in data:
                session_env["VAS_EVALUATION_JSON"] = json.dumps(
                    validate_evaluation(data["evaluation"]), ensure_ascii=False
                )
            session_env["VAS_DEVICE_ID"] = device_id.upper()
            session_env["VAS_ENVIRONMENT"] = environment
            session_env["VAS_TURNS_JSON"] = json.dumps(
                data.get("turns"), ensure_ascii=False
            )
            session_env["VAS_INPUT_MODE"] = data.get("input_mode", "manual")
            session_env["VAS_TURN_TIMEOUT_SECONDS"] = data.get(
                "turn_timeout_seconds", "90"
            )
            settings = settings_from_env(session_env)
            turns = validate_turns(session_env["VAS_TURNS_JSON"], settings)
            for turn in turns:
                if "source_audio" in turn:
                    validate_audio(
                        turn["source_audio"], settings["turn_timeout_seconds"]
                    )
        except (ValueError, TypeError, OSError, yaml.YAMLError) as exc:
            raise ValueError(f"{relative}: {exc}") from exc
        slug = (
            re.sub(r"[^a-zA-Z0-9_-]+", "-", str(Path(relative).with_suffix(""))).strip(
                "-"
            )[:80]
            or "session"
        )
        identifier = f"{slug}-{hashlib.sha256(relative.encode()).hexdigest()[:8]}"
        sessions.append(
            {
                "id": identifier,
                "source": f"scenarios/{relative}",
                "name": name.strip(),
                "environment": environment,
                "device_id": device_id.upper(),
                "env": session_env,
                "settings": settings,
                "planned_turns": turns,
            }
        )
    return sessions


def _safe_error(exc, env):
    message = f"{type(exc).__name__}: {exc}"
    token = env.get("VAS_TOKEN")
    return message.replace(token, "[redacted]") if token else message


def write_batch(output, batch):
    """Persist machine-readable status plus a portable HTML entry point."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for item in batch["sessions"]:
        report_path = output / item["directory"] / "report.json"
        if report_path.is_file():
            try:
                reports.append(json.loads(report_path.read_text()))
            except (ValueError, OSError) as exc:
                item.update(
                    status="failed",
                    failure_stage="report",
                    error=f"Invalid report: {type(exc).__name__}",
                )
                batch["status"] = "failed"
    try:
        has_excel = export_excel(reports, output / "evaluation.xlsx")
    except Exception as exc:
        batch.update(
            status="failed",
            failure_stage="report",
            error=f"Excel export failed: {type(exc).__name__}",
        )
        has_excel = False
    suites = ET.Element("testsuites", name="VAS batch")
    for item in batch["sessions"]:
        source = output / item["directory"] / "junit.xml"
        suite = None
        if source.exists():
            try:
                suite = ET.parse(source).getroot()
            except (ET.ParseError, OSError):
                item.update(
                    status="failed",
                    failure_stage="report",
                    error="Invalid JUnit report",
                )
                batch["status"] = "failed"
        if suite is not None:
            suite.set("name", item["name"])
            for case in suite.findall("testcase"):
                case.set("classname", f'voice_scenario.{item["id"]}')
            suites.append(suite)
            if item["status"] == "failed" and not suite.findall(".//failure"):
                case = ET.SubElement(
                    suite, "testcase", name=item.get("failure_stage", "session")
                )
                ET.SubElement(
                    case, "failure", message=item.get("error", "Session failed")
                )
                suite.set("tests", str(len(suite.findall(".//testcase"))))
                suite.set("failures", "1")
        elif item["status"] == "failed":
            suite = ET.SubElement(
                suites, "testsuite", name=item["name"], tests="1", failures="1"
            )
            ET.SubElement(
                ET.SubElement(suite, "testcase", name="session_report"),
                "failure",
                message=item.get(
                    "error", "Session failed before a report was available"
                ),
            )
    if batch.get("error"):
        setup = ET.SubElement(
            suites, "testsuite", name="batch_setup", tests="1", failures="1"
        )
        ET.SubElement(
            ET.SubElement(setup, "testcase", name="prepare"),
            "failure",
            message=batch["error"],
        )
    suites.set("tests", str(sum(int(suite.get("tests", "0")) for suite in suites)))
    suites.set("failures", str(len(suites.findall(".//failure"))))
    ET.ElementTree(suites).write(
        output / "junit.xml", encoding="utf-8", xml_declaration=True
    )
    (output / "batch.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    excel_link = (
        '<p><a href="evaluation.xlsx">下载 Excel 评测汇总</a></p>' if has_excel else ""
    )
    rows = []
    for index, item in enumerate(batch["sessions"], 1):
        name = html.escape(item["name"])
        if item.get("report"):
            name = f'<a href="{html.escape(item["report"], quote=True)}">{name} →</a>'
        rows.append(
            f'<tr><td>{index:02d}</td><td>{name}<small>{html.escape(item["source"])}</small></td><td>{html.escape(item.get("environment", "—"))}<small>{html.escape(item.get("device_id", "—"))}</small></td><td>{html.escape(item["status"])}<small>{html.escape(item.get("failure_stage", ""))}</small></td><td>{item.get("completed_turns", 0)}/{item.get("turn_count", "—")}</td></tr>'
        )
    error = (
        f'<p class="error">{html.escape(batch["error"])}</p>'
        if batch.get("error")
        else ""
    )
    document = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VAS 批量测试</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f5f7fa;color:#192637;margin:0}}main{{max-width:1000px;margin:48px auto;padding:24px}}h1{{margin-bottom:8px}}p{{color:#526477}}table{{width:100%;border-collapse:collapse;background:white;border:1px solid #dce2e9;border-radius:12px}}th,td{{text-align:left;padding:16px;border-bottom:1px solid #e6ebf0}}th{{color:#64748b;font-size:13px}}a{{color:#1963d1;text-decoration:none;font-weight:600}}a:hover{{text-decoration:underline}}small{{display:block;color:#748295;font-size:12px}}.error{{color:#a42338;background:#fff1f2;padding:16px}}@media(max-width:600px){{main{{margin:12px auto;padding:12px}}th,td{{padding:10px}}small{{overflow-wrap:anywhere}}}}</style>
<main><small>AQUAMIND · VOICE TESTING</small><h1>批量会话测试</h1><p>{html.escape(batch["status"])} · {len(batch["sessions"])} 个 session · {html.escape(batch.get("environment", ""))}</p>{'<p>MOCK 模拟数据 · 不代表真实服务性能</p>' if batch.get('data_source') == 'mock' else ''}{excel_link}{error}<table><thead><tr><th>#</th><th>会话</th><th>环境 / MAC</th><th>结果 / 阶段</th><th>完成 / 计划</th></tr></thead><tbody>{"".join(rows)}</tbody></table></main></html>"""
    for name in ("index.html", "report.html"):
        (output / name).write_text(document, encoding="utf-8")


async def execute(source, output, env, *, prepare_only=False, mock=False):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output already contains files; choose a new directory")
    batch = {
        "schema_version": 1,
        "status": "preparing",
        "environment": "",
        "sessions": [],
        "data_source": "mock" if mock else "live",
    }
    try:
        sessions = load_sessions(Path(source), env)
    except Exception as exc:
        batch.update(
            status="failed", failure_stage="validation", error=_safe_error(exc, env)
        )
        write_batch(output, batch)
        logging.error("%s", batch["error"])
        return 2
    environments = {item["environment"] for item in sessions}
    batch["environment"] = (
        next(iter(environments)) if len(environments) == 1 else "mixed"
    )
    for session in sessions:
        item = {
            key: session[key]
            for key in ("id", "source", "name", "environment", "device_id")
        }
        item.update(
            directory=f'sessions/{session["id"]}',
            status="pending",
            turn_count=len(json.loads(session["env"]["VAS_TURNS_JSON"])),
            completed_turns=0,
        )
        batch["sessions"].append(item)
    batch["status"] = "running"
    write_batch(output, batch)
    for item, session in zip(batch["sessions"], sessions):
        session_env = session["env"]
        directory = output / item["directory"]
        stage = "prepare"
        settings = session["settings"]
        # Retain the static validation result; runtime file failures stay in prepare.
        planned = session["planned_turns"]
        metadata = dict(
            data_source=batch["data_source"],
            run_metadata=settings,
            evaluation=json.loads(session_env.get("VAS_EVALUATION_JSON", "null")),
            evaluation_turns=[
                dict(id=t["id"], tool=t.get("tool"), input_text=t.get("input_text", ""))
                for t in planned
            ],
        )
        try:
            item["status"] = "preparing"
            write_batch(output, batch)
            settings, scenario = prepare(directory, session_env, name=item["name"])
            item["status"] = "prepared" if prepare_only else "running"
            write_batch(output, batch)
            if not prepare_only:
                stage = "run"
                if mock:
                    from .mock_run import execute_mock

                    code = execute_mock(directory, settings, scenario)
                else:
                    code = await execute_prepared(
                        directory, session_env, settings, scenario
                    )
                stage = "report"
                json.loads((directory / "report.json").read_text())
                result = json.loads((directory / "result.json").read_text())
                item["completed_turns"] = sum(
                    t.get("status") != "failed" for t in result.get("turns", [])
                )
                item["status"] = "passed" if code == 0 else "failed"
                if code:
                    item["failure_stage"] = result.get("failure_stage") or (
                        "assertion" if result.get("status") == "passed" else "run"
                    )
                    item["failure_reasons"] = read_failure_reasons(
                        directory, token=session_env.get("VAS_TOKEN", "")
                    )
        except Exception as exc:
            stage = getattr(exc, "stage", stage)
            error = _safe_error(exc, session_env)
            item.update(
                status="failed",
                failure_stage=stage,
                error=error,
                failure_reasons=[error],
            )
            logging.error("%s | %s | %s", item["name"], stage, error)
            write_failed_session_report(directory, item["name"], stage, error, metadata)
        if (directory / "report.html").exists():
            item["report"] = f'{item["directory"]}/report.html'
        write_batch(output, batch)
        logging.info("%s | %s", item["status"].upper(), item["source"])
    expected = "prepared" if prepare_only else "passed"
    batch["status"] = (
        expected
        if not batch.get("error")
        and all(item["status"] == expected for item in batch["sessions"])
        else "failed"
    )
    write_batch(output, batch)
    return 0 if batch["status"] == expected else 1


def summarize(directory):
    path = Path(directory) / "batch.json"
    if not path.is_file():
        return "## VAS 批量测试\n\n没有生成批量报告，请查看执行步骤日志。\n"
    batch = json.loads(path.read_text(encoding="utf-8"))
    from .ci_run import _markdown

    lines = [
        "## VAS 批量测试",
        "",
        f'**{_markdown(batch["status"])}** · {len(batch["sessions"])} 个 session',
        "",
        "| 会话 | 环境 | MAC | 结果 | 完成/计划 | 失败阶段 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in batch["sessions"]:
        lines.append(
            f'| {_markdown(item["name"])} | {_markdown(item.get("environment"))} | {_markdown(item.get("device_id"))} | {_markdown(item["status"])} | {item.get("completed_turns", 0)}/{item.get("turn_count", "—")} | {_markdown(item.get("failure_stage"))} |'
        )
    for item in batch["sessions"]:
        if item["status"] != "failed":
            continue
        reasons = (
            read_failure_reasons(Path(directory) / item["directory"])
            or item.get("failure_reasons", [])
            or failure_reasons(item)
        )
        lines.append(f'\n**{_markdown(item["name"])}**\n')
        lines.extend(f"- {_markdown(reason)}" for reason in reasons)
        if item.get("report"):
            lines.append(f'\n报告文件：<code>{html.escape(item["report"])}</code>')
    if batch.get("error"):
        lines.append(f'\n{_markdown(batch["error"])}')
    lines.append("\n下载完整报告并解压，打开 `index.html` 选择 session。\n")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=Path("scenarios/smoke"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/batch"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Generate deterministic local diagnostic fixtures; never connect to VAS",
    )
    parser.add_argument("--summarize", type=Path)
    parser.add_argument(
        "--matrix-output",
        type=Path,
        help="Validate and export Actions session matrix without connecting",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.summarize:
        summary = summarize(args.summarize)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open(
                "a", encoding="utf-8"
            ) as target:
                target.write(summary)
        else:
            logging.warning("%s", summary)
        return
    try:
        if args.matrix_output:
            sessions = load_sessions(args.scenarios, os.environ)
            matrix = {
                "include": [
                    {
                        key: item[key]
                        for key in ("id", "name", "source", "environment", "device_id")
                    }
                    for item in sessions
                ]
            }
            args.matrix_output.parent.mkdir(parents=True, exist_ok=True)
            args.matrix_output.write_text(
                json.dumps(matrix, ensure_ascii=False), encoding="utf-8"
            )
            return
        code = asyncio.run(
            execute(
                args.scenarios,
                args.output,
                os.environ,
                prepare_only=args.prepare_only,
                mock=args.mock,
            )
        )
    except Exception as exc:
        logging.error("%s", _safe_error(exc, os.environ))
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
