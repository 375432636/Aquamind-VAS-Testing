"""Environment-based, shell-safe text/audio runner used by GitHub Actions."""

import argparse
import asyncio
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .__main__ import create_report
from .model import Scenario
from .runner import run_scenario, save_result
from .websocket import WebSocketTransport

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = {
    "dev": "wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/",
    "main": "wss://lumin-vas-aquamind.deep-edge.cn/looomyn/v1/",
}
NUMERIC_METRICS = {
    "first_playback_ms",
    "first_answer_playback_ms",
    "stop_queue_ms",
    "stop_to_execution_ms",
    "asr_commit_to_final_ms",
    "local_vad_silence_ms",
    "asr_endpoint_to_final_ms",
    "local_vad_starts",
    "local_vad_ends",
    "asr_vad_starts",
    "asr_vad_ends",
    "llm_requests",
    "memory_requests",
    "tts_requests",
    "pre_speech_outputs",
    "filler_outputs",
    "knowledge_base_calls",
    "interrupt_lateness_ms",
}


def _validate_expect(expected, diagnostics):
    if not isinstance(expected, dict):
        raise ValueError("expect must be an object")
    for name, value in expected.items():
        metric = name.removeprefix("max_").removesuffix("_min")
        if diagnostics == "off" and metric not in {
            "first_playback_ms",
            "interrupt_lateness_ms",
            "asr_text",
        }:
            raise ValueError(
                "Internal metric assertions require stage/frame diagnostics"
            )
        if name == "asr_text" and isinstance(value, str):
            continue
        if (
            name == "tools"
            and isinstance(value, dict)
            and all(
                isinstance(key, str) and type(count) is int and count >= 0
                for key, count in value.items()
            )
        ):
            continue
        if (
            metric in NUMERIC_METRICS
            and type(value) in {int, float}
            and math.isfinite(value)
            and value >= 0
        ):
            continue
        raise ValueError("expect contains an unsupported metric or invalid value")


def _number(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return number


def settings_from_env(env):
    environment = env.get("VAS_ENVIRONMENT", "dev")
    if environment not in ENDPOINTS:
        raise ValueError("VAS_ENVIRONMENT must be dev or main")
    url = env.get(f"VAS_{environment.upper()}_URL") or ENDPOINTS[environment]
    parts = urlsplit(url)
    if (
        parts.scheme not in {"ws", "wss"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "VAS URL requires ws/wss without credentials or query parameters"
        )
    if parts.scheme == "ws" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote VAS requires wss")
    device = env.get("VAS_DEVICE_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", device):
        raise ValueError(
            "VAS_DEVICE_ID is required and must be a valid device identifier"
        )
    mode = env.get("VAS_INPUT_MODE", "manual")
    if mode not in {"manual", "vad"}:
        raise ValueError("VAS_INPUT_MODE must be manual or vad")
    diagnostics = env.get("VAS_DIAGNOSTICS", "stage")
    if diagnostics not in {"off", "stage", "frame"}:
        raise ValueError("VAS_DIAGNOSTICS must be off, stage or frame")
    timeout = _number(env.get("VAS_TURN_TIMEOUT_SECONDS", "90"), "turn timeout", 5, 120)
    return {
        "environment": environment,
        "endpoint": url,
        "device_id": device,
        "input_mode": mode,
        "diagnostics": diagnostics,
        "turn_timeout_seconds": timeout,
        "speech_engine": "espeak-ng/cmn",
    }


def validate_turns(raw, settings):
    try:
        turns = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("VAS_TURNS_JSON must be a JSON array") from exc
    if not isinstance(turns, list) or not 1 <= len(turns) <= 30:
        raise ValueError("VAS_TURNS_JSON requires 1–30 turns")
    allowed = {
        "text",
        "audio",
        "id",
        "interrupt_after_seconds",
        "output_kind",
        "expect",
    }
    normalized, ids = [], set()
    for index, turn in enumerate(turns, 1):
        if isinstance(turn, str):
            turn = {"text": turn}
        if not isinstance(turn, dict) or set(turn) - allowed:
            raise ValueError(f"turn {index}: unsupported fields")
        if ("text" in turn) == ("audio" in turn):
            raise ValueError(f"turn {index}: provide exactly one of text or audio")
        turn_id = turn.get("id", f"turn-{index:03d}")
        if (
            not isinstance(turn_id, str)
            or not re.fullmatch(r"[\w-]{1,80}", turn_id)
            or turn_id in ids
        ):
            raise ValueError(
                f"turn {index}: id must be unique letters, numbers, underscores or hyphens"
            )
        ids.add(turn_id)
        item = {"id": turn_id}
        if "text" in turn:
            text = turn["text"]
            if (
                not isinstance(text, str)
                or not 1 <= len(text.strip()) <= 1000
                or "\x00" in text
            ):
                raise ValueError(f"turn {index}: text must contain 1–1000 characters")
            item["input_text"] = text.strip()
        else:
            if not isinstance(turn["audio"], str):
                raise ValueError(f"turn {index}: audio must be a repository WAV path")
            audio = (ROOT / turn["audio"]).resolve()
            if (
                not audio.is_relative_to(ROOT)
                or audio.suffix.lower() != ".wav"
                or not audio.is_file()
            ):
                raise ValueError(
                    f"turn {index}: audio must be an existing WAV inside the repository"
                )
            item["source_audio"] = audio
        if "interrupt_after_seconds" in turn:
            delay = _number(
                turn["interrupt_after_seconds"], "interrupt_after_seconds", 0, 110
            )
            if delay >= settings["turn_timeout_seconds"]:
                raise ValueError(
                    f"turn {index}: interrupt delay must be shorter than turn timeout"
                )
            kind = turn.get("output_kind", "any")
            if kind not in {"any", "answer", "pre_speech", "filler"}:
                raise ValueError(f"turn {index}: unsupported output_kind")
            if settings["diagnostics"] == "off" and kind != "any":
                raise ValueError(
                    "Output-kind-specific interruption requires stage/frame diagnostics"
                )
            item["interrupt"] = {"after_playback_seconds": delay, "output_kind": kind}
        elif "output_kind" in turn:
            raise ValueError(
                f"turn {index}: output_kind requires interrupt_after_seconds"
            )
        if "expect" in turn:
            _validate_expect(turn["expect"], settings["diagnostics"])
            item["expect"] = turn["expect"]
        normalized.append(item)
    return normalized


def synthesize(text, target):
    """Write real Mandarin speech; text goes over stdin, never into shell code."""
    if not shutil.which("espeak-ng") or not shutil.which("ffmpeg"):
        raise RuntimeError("Install espeak-ng and ffmpeg before generating speech")
    with tempfile.TemporaryDirectory(prefix="vas-speech-") as temporary:
        intermediate = Path(temporary) / "speech.wav"
        subprocess.run(
            [
                "espeak-ng",
                "-v",
                "cmn",
                "-s",
                "165",
                "-b",
                "1",
                "-w",
                str(intermediate),
                "--stdin",
            ],
            input=text,
            text=True,
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(intermediate),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )


def validate_audio(path, timeout):
    with wave.open(str(path), "rb") as audio:
        if (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            audio.getcomptype(),
        ) != (1, 2, 16000, "NONE"):
            raise ValueError("Input WAV must be mono 16 kHz PCM16")
        if not 0 < audio.getnframes() / 16000 < timeout - 1:
            raise ValueError(
                "Input speech must be nonempty and shorter than the turn timeout"
            )
        if len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
            raise ValueError("Input WAV is truncated")


def prepare(output, env, *, name=None):
    settings = settings_from_env(env)
    turns = validate_turns(env.get("VAS_TURNS_JSON"), settings)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "scenario.yaml").exists() or (output / "result.json").exists():
        raise ValueError("Output already contains a run; choose a new directory")
    inputs = output / "inputs"
    inputs.mkdir(exist_ok=True)
    for index, turn in enumerate(turns, 1):
        audio = inputs / f"turn-{index:03d}.wav"
        if "source_audio" in turn:
            shutil.copyfile(turn.pop("source_audio"), audio)
        else:
            synthesize(turn["input_text"], audio)
        validate_audio(audio, settings["turn_timeout_seconds"])
        turn["audio"] = audio.relative_to(output).as_posix()
    scenario = {
        "name": name
        or f"Aquamind {settings['environment'].upper()} · {len(turns)} 轮语音测试",
        "turn_timeout_seconds": settings["turn_timeout_seconds"],
        "input": {"mode": settings["input_mode"]},
        "turns": turns,
    }
    (output / "scenario.yaml").write_text(
        yaml.safe_dump(scenario, allow_unicode=True, sort_keys=False)
    )
    (output / "run-settings.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2)
    )
    return settings, Scenario.from_dict(scenario, base_dir=output)


async def execute(output, env):
    settings, scenario = prepare(output, env)
    return await execute_prepared(output, env, settings, scenario)


async def execute_prepared(output, env, settings, scenario):
    """Execute a validated session; callers can prepare a complete batch first."""
    transport = WebSocketTransport(
        settings["endpoint"],
        device_id=settings["device_id"],
        token=env.get("VAS_TOKEN") or None,
        diagnostics=settings["diagnostics"],
    )
    result = await run_scenario(scenario, transport, output)
    result["run_metadata"] = settings
    save_result(result, Path(output))
    report = create_report(output)
    return 0 if report["status"] == "passed" else 1


def _markdown(value):
    value = html.escape(str(value or "—")).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", value)


def _seconds(value):
    return "—" if value is None else f"{value / 1000:.3f} s"


def summarize(directory):
    path = Path(directory) / "report.json"
    if not path.is_file():
        return "## VAS 语音测试\n\n运行未生成报告，请查看测试步骤日志。\n"
    report = json.loads(path.read_text())
    lines = [
        "## VAS 语音测试",
        "",
        f"**{_markdown(report.get('status'))}** · {_markdown(report.get('name'))}",
        "",
        "| 轮次 | 输入 | 状态 | 首音等待 | 正式首音等待 | 最长句间空档 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, turn in enumerate(report["turns"], 1):
        metrics = turn["metrics"]
        gap = turn.get("reply_timing", {}).get("max_gap_seconds")
        status = (
            "failed"
            if any(not check["passed"] for check in turn.get("checks", []))
            else turn["status"]
        )
        lines.append(
            f"| {index} | {_markdown((turn.get('input_text') or metrics.get('asr_text') or turn['id'])[:100])} | {_markdown(status)} | {_seconds(metrics.get('first_playback_ms'))} | {_seconds(metrics.get('first_answer_playback_ms'))} | {_seconds(None if gap is None else gap * 1000)} |"
        )
    for error in report.get("failures", []) + (
        [report["error"]] if report.get("error") else []
    ):
        lines.append(f"\n- {_markdown(error)}")
    lines.append(
        "\n下载完整报告，解压后打开 `index.html`；点击每轮查看时序和逐段音频。\n"
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/run"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--summarize", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.summarize:
        summary = summarize(args.summarize)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
                output.write(summary)
        else:
            logging.warning("%s", summary)
        return
    existing = (args.output / "result.json").exists() or (
        args.output / "scenario.yaml"
    ).exists()
    try:
        if args.prepare_only:
            prepare(args.output, os.environ)
            code = 0
        else:
            code = asyncio.run(execute(args.output, os.environ))
    except Exception as exc:
        # Do not persist headers or the raw environment, even for startup errors.
        message = f"{type(exc).__name__}: {exc}"
        token = os.getenv("VAS_TOKEN")
        if token:
            message = message.replace(token, "[redacted]")
        logging.error("%s", message)
        if not existing and not (args.output / "result.json").exists():
            args.output.mkdir(parents=True, exist_ok=True)
            save_result(
                {
                    "name": "VAS 测试未完成",
                    "status": "failed",
                    "turns": [],
                    "error": message,
                },
                args.output,
            )
            create_report(args.output)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
