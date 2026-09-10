"""Exercise the same environment-based entry point as GitHub Actions."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import opuslib_next as opuslib
import pytest
import yaml
from websockets.asyncio.server import serve

from voice_scenarios.__main__ import create_report

ROOT = Path(__file__).resolve().parents[1]


def invoke(output, turns, *flags, **settings):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VAS_")}
    env.update(
        VAS_ENVIRONMENT="dev",
        VAS_DEVICE_ID="AA:BB:CC:DD:EE:91",
        VAS_TURNS_JSON=json.dumps(turns, ensure_ascii=False),
        **settings,
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_scenarios.ci_run",
            "--output",
            str(output),
            *flags,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_actions_text_becomes_audio_with_per_turn_interrupt_without_credentials(
    tmp_path,
):
    output = tmp_path / "run"
    outcome = invoke(
        output,
        [
            {
                "text": "你好，介绍一下耳机。",
                "interrupt_after_seconds": 2,
                "output_kind": "answer",
            },
            {"text": "第一个有什么特点？"},
        ],
        "--prepare-only",
        VAS_TOKEN="test-token-must-never-be-saved",
    )
    assert outcome.returncode == 0, outcome.stderr
    scenario = yaml.safe_load((output / "scenario.yaml").read_text())
    assert len(scenario["turns"]) == 2
    assert scenario["turns"][0]["input_text"] == "你好，介绍一下耳机。"
    assert scenario["turns"][0]["interrupt"] == {
        "after_playback_seconds": 2,
        "output_kind": "answer",
    }
    assert "interrupt" not in scenario["turns"][1]
    for turn in scenario["turns"]:
        with wave.open(str(output / turn["audio"]), "rb") as audio:
            assert (
                audio.getnchannels(),
                audio.getsampwidth(),
                audio.getframerate(),
            ) == (1, 2, 16000)
            assert 0 < audio.getnframes() / audio.getframerate() < 30
            assert any(audio.readframes(audio.getnframes()))
    assert "test-token-must-never-be-saved" not in outcome.stderr + outcome.stdout
    for path in output.rglob("*"):
        if path.is_file():
            assert b"test-token-must-never-be-saved" not in path.read_bytes()


def test_actions_entry_point_reuses_session_after_interrupt_and_exports_report(
    tmp_path,
):
    output = tmp_path / "wire-run"
    received = []

    async def exercise():
        async def peer(ws):
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            async for raw in ws:
                if isinstance(raw, bytes):
                    received.append("audio")
                    continue
                message = json.loads(raw)
                received.append((message["type"], message.get("state")))
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "one-session",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )
                elif message["type"] == "listen" and message["state"] == "stop":
                    await ws.send(json.dumps({"type": "tts", "state": "start"}))
                    await ws.send(
                        json.dumps(
                            {
                                "type": "tts",
                                "state": "sentence_start",
                                "text": "这是一段测试回复。",
                            }
                        )
                    )
                    for _ in range(8):
                        await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))
                elif message["type"] == "abort":
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))

        async with serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await asyncio.to_thread(
                invoke,
                output,
                [{"text": "你好", "interrupt_after_seconds": 0.1}, {"text": "继续"}],
                VAS_DEV_URL=f"ws://127.0.0.1:{port}/looomyn/v1/",
                VAS_DIAGNOSTICS="off",
            )

    outcome = asyncio.run(exercise())
    assert outcome.returncode == 0, outcome.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["session"]["session_id"] == "one-session"
    assert [t["input_text"] for t in report["turns"]] == ["你好", "继续"]
    assert [t["status"] for t in report["turns"]] == ["interrupted", "completed"]
    assert received.count(("hello", None)) == 1
    assert received.count(("abort", None)) == 1
    assert received.count(("listen", "stop")) == 2
    assert received.count("audio") > 0
    assert report["turns"][0]["interruption"]["client_playback_stopped"]
    assert (output / "report.html").is_file()
    assert (output / "junit.xml").is_file()
    assert (
        report["turns"][1]["reply_timing"]["sentences"][0]["audio"]["played"]["status"]
        == "ready"
    )
    if os.getenv("CI_REPORT_DIR"):
        example = Path(os.environ["CI_REPORT_DIR"])
        shutil.copytree(output, example, dirs_exist_ok=True)
        result = json.loads((example / "result.json").read_text())
        result["name"] = "CI 示例 · 本地 WebSocket 与合成语音"
        result["run_metadata"]["environment"] = "local"
        (example / "result.json").write_text(json.dumps(result, ensure_ascii=False))
        create_report(example)


@pytest.mark.parametrize(
    "turns, settings",
    [
        ([], {}),
        ([{"text": ""}], {}),
        ([{"text": "你好", "interupt_after_seconds": 2}], {}),
        ([{"text": "你好", "interrupt_after_seconds": -1}], {}),
        ([{"text": "你好", "output_kind": "answer"}], {}),
        (
            [{"text": "你好", "interrupt_after_seconds": 1, "output_kind": "answer"}],
            {"VAS_DIAGNOSTICS": "off"},
        ),
        (["你好"], {"VAS_DEV_URL": "wss://user:password@example.com/v1/"}),
        ([{"audio": "../outside.wav"}], {}),
    ],
)
def test_invalid_action_inputs_fail_with_downloadable_report(tmp_path, turns, settings):
    outcome = invoke(tmp_path / "invalid", turns, "--prepare-only", **settings)
    assert outcome.returncode == 2
    report = json.loads((tmp_path / "invalid/report.json").read_text())
    assert report["status"] == "failed"
    assert report["error"]
    assert (tmp_path / "invalid/report.html").is_file()
    assert not list((tmp_path / "invalid").glob("inputs/*.wav"))


def test_invalid_regression_threshold_is_rejected_before_audio_is_generated(tmp_path):
    outcome = invoke(
        tmp_path / "invalid",
        [{"text": "你好", "expect": {"max_first_playback_ms": "fast"}}],
        "--prepare-only",
    )
    assert outcome.returncode == 2
    assert not list((tmp_path / "invalid").glob("inputs/*.wav"))
