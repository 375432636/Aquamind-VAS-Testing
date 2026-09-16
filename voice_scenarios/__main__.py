import argparse
import asyncio
import hashlib
import json
import logging
import platform
import shutil
import statistics
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from .dev_stack import ROOT, serve_stack
from .failure_summary import failure_reasons
from .model import SENSOR_COMMANDS, Scenario, Turn
from .reply_audio import prepare_reply_audio
from .report import build_report, evaluate
from .report_archive import restore_audio
from .runner import run_scenario
from .session_timing import prepare_session_playback
from .websocket import WebSocketTransport


def create_report(directory):
    directory = Path(directory)
    restore_audio(directory)
    result = json.loads((directory / "result.json").read_text())
    path = directory / "vas-events.jsonl"
    events = (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )
    report = evaluate(result, events, artifact_dir=directory)
    prepare_reply_audio(report, directory)
    prepare_session_playback(report, directory)
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    build_report(report, directory / "report.html")
    metrics = {t["id"]: t["metrics"] for t in report["turns"]}
    (directory / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2)
    )
    suite = ET.Element(
        "testsuite", name=report["name"], tests=str(len(report["turns"]) + 1)
    )
    for turn in report["turns"]:
        case = ET.SubElement(
            suite, "testcase", name=turn["id"], classname="voice_scenario"
        )
        failed = [c["name"] for c in turn["checks"] if not c["passed"]]
        if turn["status"] == "failed" or failed:
            ET.SubElement(
                case, "failure", message=turn.get("error") or ", ".join(failed)
            ).text = json.dumps(turn["checks"], ensure_ascii=False)
    trace_case = ET.SubElement(
        suite, "testcase", name="capture_integrity", classname="voice_scenario"
    )
    if report.get("diagnostics") and not report["diagnostics"].get("complete"):
        ET.SubElement(trace_case, "failure", message="Incomplete diagnostic capture")
    if report["status"] == "failed" and not suite.findall(".//failure"):
        reasons = failure_reasons(report)
        ET.SubElement(trace_case, "failure", message="; ".join(reasons)).text = (
            "\n".join(reasons) + "\n详细报告：report.html"
        )
    suite.set("failures", str(len(suite.findall(".//failure"))))
    ET.ElementTree(suite).write(
        directory / "junit.xml", encoding="utf-8", xml_declaration=True
    )
    manifest = {
        "schema_version": 1,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "session": report.get("session"),
        "diagnostics": report.get("diagnostics"),
        "inputs": [],
    }
    for turn in report["turns"]:
        filename = turn.get("audio", {}).get("input")
        audio = Path(filename) if filename else None
        if turn.get("sensor"):
            manifest["inputs"].append({"turn_id": turn["id"], "sensor": turn["sensor"]})
        if audio and audio.is_file():
            manifest["inputs"].append(
                {
                    "turn_id": turn["id"],
                    "filename": audio.name,
                    "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                }
            )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return report


async def run(args):
    config = yaml.safe_load(args.config.read_text()) if args.config else {}
    url = args.url or config.get("url", "ws://127.0.0.1:19080")
    diagnostics = args.diagnostics or config.get("diagnostics", "off")
    scenario = (
        Scenario.load(args.scenario)
        if args.scenario
        else (
            Scenario.from_dict({"name": "sensor", "turns": [{"sensor": args.sensor}]})
            if getattr(args, "sensor", None)
            else Scenario("single-audio", (Turn("audio", args.audio.resolve()),))
        )
    )
    if diagnostics == "off" and any(
        t.interrupt and t.interrupt.output_kind not in {"any", "music"}
        for t in scenario.turns
    ):
        raise ValueError(
            "Output-kind-specific interruption requires stage/frame diagnostics"
        )
    output = args.output or ROOT / "artifacts" / datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )
    if (output / "result.json").exists():
        raise ValueError("Output already contains a run; choose a new directory")
    all_reports = []
    for i in range(args.repeat):
        target = output / f"run-{i+1:03d}" if args.repeat > 1 else output
        transport = WebSocketTransport(
            url,
            device_id=args.device_id or config.get("device_id", "AA:BB:CC:DD:EE:91"),
            token=config.get("token"),
            diagnostics=diagnostics,
            fake_device_tools=args.fake_device
            or config.get("fake_device_tools", False),
        )
        await run_scenario(scenario, transport, target)
        report = create_report(target)
        all_reports.append(report)
        logging.warning("%s | %s", report["status"].upper(), target / "report.html")
        for failure in report.get("failures", []):
            logging.error("%s", failure)
    if args.repeat > 1:
        values = [
            t["metrics"]["first_answer_playback_ms"]
            for r in all_reports
            for t in r["turns"]
            if t["metrics"]["first_answer_playback_ms"] is not None
        ]
        summary = {
            "runs": len(all_reports),
            "passed": sum(r["status"] == "passed" for r in all_reports),
            "sample_count": len(values),
            "first_answer_p50_ms": statistics.median(values) if values else None,
            "first_answer_p95_ms": (
                sorted(values)[max(0, __import__("math").ceil(0.95 * len(values)) - 1)]
                if values
                else None
            ),
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
    return 0 if all(r["status"] == "passed" for r in all_reports) else 1


def main():
    parser = argparse.ArgumentParser(
        description="Python voice scenario tests. run connects to existing VAS; stack starts local services."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    runp = sub.add_parser("run")
    runp.add_argument("--config", type=Path)
    source = runp.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenario", type=Path)
    source.add_argument("--audio", type=Path)
    source.add_argument("--sensor", choices=list(SENSOR_COMMANDS))
    runp.add_argument("--url")
    runp.add_argument("--device-id")
    runp.add_argument("--diagnostics", choices=["off", "stage", "frame"])
    runp.add_argument("--fake-device", action="store_true")
    runp.add_argument("--output", type=Path)
    runp.add_argument("--repeat", type=int, default=1)
    stack = sub.add_parser("stack")
    stack.add_argument("--vas-root", type=Path, required=True)
    stack.add_argument(
        "--script", type=Path, default=ROOT / "config/fake-regression.yaml"
    )
    stack.add_argument("--base-port", type=int, default=19080)
    reportp = sub.add_parser("report")
    reportp.add_argument("directory", type=Path)
    rebuild = sub.add_parser("rebuild")
    rebuild.add_argument("directory", type=Path)
    rebuild.add_argument("--output", type=Path, required=True)
    webp = sub.add_parser("web")
    webp.add_argument("--output", type=Path, default=ROOT / "artifacts/live")
    webp.add_argument("--host", default="127.0.0.1")
    webp.add_argument("--port", type=int, default=19225)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "web":
            from .web_server import serve

            serve(args.output, args.host, args.port)
            code = 0
        elif args.command == "stack":
            asyncio.run(serve_stack(args.vas_root, args.script, args.base_port))
            code = 0
        elif args.command == "rebuild":
            from .excel_report import export_excel
            from .replay_clock import reconstruct_simulated_playback

            if (
                args.output.resolve() == args.directory.resolve()
                or args.output.exists()
            ):
                raise ValueError("Rebuild requires a new output directory")
            shutil.copytree(args.directory, args.output)
            result = json.loads((args.output / "result.json").read_text())
            journal = [
                json.loads(line)
                for line in (args.output / "client-events.jsonl")
                .read_text()
                .splitlines()
            ]
            changed = reconstruct_simulated_playback(result, journal)
            shutil.copy2(
                args.output / "result.json", args.output / "result.original.json"
            )
            (args.output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False)
            )
            report = create_report(args.output)
            export_excel([report], args.output / "evaluation.xlsx")
            logging.warning(
                "Rebuilt %s frames | %s", changed, args.output / "report.html"
            )
            code = 0
        elif args.command == "report":
            report = create_report(args.directory)
            logging.warning("%s", args.directory / "report.html")
            code = 0 if report["status"] == "passed" else 1
        else:
            if not 1 <= args.repeat <= 100:
                parser.error("--repeat must be 1..100")
            code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        logging.error("%s: %s", type(exc).__name__, exc)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
