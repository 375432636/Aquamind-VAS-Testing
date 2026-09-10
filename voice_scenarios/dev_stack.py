"""Own two local processes; Ctrl-C stops only the processes started here."""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

import aiohttp
import yaml

ROOT = Path(__file__).resolve().parents[1]


async def wait_ready(url, process):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1)) as client:
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError("Local service exited; inspect artifacts/stack logs")
            try:
                async with client.get(url) as response:
                    if response.status == 200:
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.1)
    raise TimeoutError("Local service readiness timeout")


async def serve_stack(vas_root, script, base_port=19080):
    vas_root = Path(vas_root).resolve()
    if (vas_root / "main/xiaozhi-server").is_dir():
        vas_root /= "main/xiaozhi-server"
    python = vas_root / ".venv/bin/python"
    if not python.exists():
        raise ValueError("Create the VAS worktree .venv first; see README")
    out = ROOT / "artifacts" / f"stack-{base_port}"
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "config/vas-local.json").read_text())
    old = "19081"
    new = str(base_port + 1)
    config = json.loads(json.dumps(config).replace(old, new))
    config["server"]["port"] = base_port
    profile = yaml.safe_load(Path(script).read_text())
    config["safety_filter"] = {
        "api": {
            "base_url": f"http://127.0.0.1:{base_port+1}/v1",
            "api_key": "fake-embedding-key",
        },
        "vector_similarity": {"model": "fake-embedding", "semantic_cache_file": ""},
    }
    config["Intent"]["intent_llm"]["type"] = profile.get("intent_type", "function_call")
    config["VAD"]["SileroVAD"]["model_dir"] = str(
        vas_root / "models/snakers4_silero-vad"
    )
    config["log"].update(
        log_dir=str(out), data_dir=str(out / "data"), log_file="vas-business.log"
    )
    config["audio_diagnostics"]["tool_categories"] = {
        "self_test_rag_query": "knowledge_base",
        "self_test_weather": "other",
        "self_music_play": "music",
        "self_music_stop": "music",
    }
    path = out / "vas-config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
    processes = []
    files = []
    try:

        def launch(command, name, cwd, env=None):
            file = (out / (name + ".log")).open("w")
            files.append(file)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            processes.append(process)
            return process

        fake = launch(
            [
                sys.executable,
                "-m",
                "voice_scenarios.fake_services",
                "--port",
                str(base_port + 1),
                "--script",
                str(Path(script).resolve()),
                "--pcm-fixture",
                str(ROOT / "fixtures/reply.wav"),
            ],
            "fake",
            ROOT,
        )
        await wait_ready(f"http://127.0.0.1:{base_port+1}/health", fake)
        env = (
            dict(os.environ, DYLD_LIBRARY_PATH="/opt/homebrew/lib")
            if sys.platform == "darwin"
            else os.environ.copy()
        )
        vas = launch(
            [
                str(python),
                str(ROOT / "voice_scenarios/vas_bootstrap.py"),
                "--vas-root",
                str(vas_root),
                "--config",
                str(path),
            ],
            "vas",
            vas_root,
            env,
        )
        await wait_ready(f"http://127.0.0.1:{base_port}", vas)
        logging.warning(
            "READY: VAS ws://127.0.0.1:%s | fake http://127.0.0.1:%s | logs %s",
            base_port,
            base_port + 1,
            out,
        )
        while all(p.poll() is None for p in processes):
            await asyncio.sleep(0.5)
        raise RuntimeError("A local service exited unexpectedly")
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(p.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    await asyncio.to_thread(p.wait)
        for file in files:
            file.close()
