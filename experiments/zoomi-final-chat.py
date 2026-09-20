"""Local multi-turn acceptance against regular 5090, using shared Piper WAVs."""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path

import yaml

from voice_scenarios.__main__ import create_report
from voice_scenarios.excel_report import export_excel
from voice_scenarios.model import Scenario
from voice_scenarios.runner import run_scenario
from voice_scenarios.speech import synthesize
from voice_scenarios.websocket import WebSocketTransport

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
MAC = "C2:B8:56:7B:95:7C"


def prepare(root: Path, *, scenario_path: Path | None = None) -> None:
    """Generate input audio once so PTT and VAD use exactly the same samples."""
    selected = scenario_path or (
        ROOT / "scenarios/continuation/zoomi-5090/05-final-chat-ptt.yaml"
    )
    spec = yaml.safe_load(selected.read_text())
    fixtures = root / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    turns = []
    for index, item in enumerate(spec["turns"], 1):
        turn = {"id": f"turn-{index:03}", "tool": item["tool"]}
        parts = item.get("chunks") or [item]
        chunks = []
        for number, part in enumerate(parts, 1):
            path = fixtures / f"{index:02}-{number:02}.wav"
            if not path.exists():
                synthesize(part["text"], path)
            chunks.append(
                {
                    "audio": str(path),
                    "resume_after_endpoint_ms": part.get("resume_after_endpoint_ms", 0),
                }
            )
        if "chunks" in item:
            turn.update(
                chunks=chunks,
                input_text="\n".join(p["text"] for p in parts),
                expect=item.get("expect", {}),
            )
        else:
            turn.update(audio=chunks[0]["audio"], input_text=item["text"])
        if "interrupt_after_seconds" in item:
            turn["interrupt"] = {
                "after_playback_seconds": item["interrupt_after_seconds"],
                "output_kind": item["output_kind"],
            }
        turns.append(turn)
    for mode in ("manual", "vad"):
        data = {
            "name": f"{spec['name']} · {'PTT' if mode == 'manual' else 'VAD'} · {len(turns)} 轮",
            "evaluation": spec["evaluation"],
            "turn_timeout_seconds": 120,
            "greeting_wait_seconds": 5,
            "greeting_timeout_seconds": 60,
            "settle_seconds": 0.5,
            "input": {"mode": mode, "noise_seed": 12345},
            "turns": turns,
        }
        Scenario.from_dict(data, base_dir=root)
        (root / f"{mode}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in fixtures.glob("*.wav")
    }
    (root / "input-manifest.json").write_text(json.dumps(manifest, indent=2))
    LOG.warning("Prepared %s shared input WAVs", len(manifest))


async def run(root: Path, mode: str, attempt: str) -> None:
    """Keep one WebSocket session for all selected turns; retain failed turns."""
    data = json.loads((root / f"{mode}.json").read_text())
    output = root / attempt / mode
    if output.exists():
        raise RuntimeError(f"Refusing to overwrite {output}")
    scenario = Scenario.from_dict(data, base_dir=root)
    transport = WebSocketTransport(
        "ws://127.0.0.1:19553/looomyn/v1/", device_id=MAC, diagnostics="frame"
    )
    LOG.warning("Starting %s: one session, %s turns", mode, len(scenario.turns))
    await run_scenario(scenario, transport, output)
    result_path = output / "result.json"
    result = json.loads(result_path.read_text())
    result["run_metadata"] = {
        "environment": "5090",
        "device_id": MAC,
        "target_host": "100.114.113.70:18443 regular VAS",
        "diagnostics": "frame",
        "speculative_chat": False,
        "input_mode": mode,
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (output / "scenario.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    report = create_report(output)
    export_excel([report], output / "evaluation.xlsx")
    LOG.warning(
        "SESSION_RESULT %s",
        json.dumps(
            {
                "mode": mode,
                "status": report["status"],
                "turns": [
                    {
                        "id": t["id"],
                        "status": t["status"],
                        "error": t.get("error"),
                        "metrics": t["metrics"],
                    }
                    for t in report["turns"]
                ],
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--scenario", type=Path, help="Input YAML for --prepare")
    parser.add_argument("--mode", choices=("manual", "vad"))
    parser.add_argument("--attempt", default="acceptance")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR, format="%(asctime)s %(message)s")
    LOG.setLevel(logging.WARNING)
    if args.prepare:
        prepare(args.root.resolve(), scenario_path=args.scenario)
    else:
        asyncio.run(run(args.root.resolve(), args.mode, args.attempt))
