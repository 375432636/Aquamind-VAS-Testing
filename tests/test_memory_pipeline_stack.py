"""Opt-in: real VAS plus Fake HTTP/WS services, including speculative tools."""

import asyncio
import json
import os
from pathlib import Path

import aiohttp
import pytest
import yaml

from voice_scenarios.__main__ import create_report
from voice_scenarios.dev_stack import ROOT, serve_stack
from voice_scenarios.excel_report import export_excel
from voice_scenarios.model import Scenario
from voice_scenarios.runner import run_scenario
from voice_scenarios.websocket import WebSocketTransport


@pytest.mark.skipif(
    not os.getenv("VAS_TEST_ROOT"), reason="Set VAS_TEST_ROOT for real VAS"
)
@pytest.mark.parametrize("mode", ["manual", "vad"])
def test_memory_pipeline_over_real_websocket(tmp_path, mode):
    async def exercise():
        port = 19420 if mode == "manual" else 19424
        task = asyncio.create_task(
            serve_stack(
                Path(os.environ["VAS_TEST_ROOT"]),
                ROOT / "config/fake-memory-pipeline.yaml",
                port,
            )
        )
        try:
            async with aiohttp.ClientSession() as client:
                for _ in range(300):
                    if task.done():
                        await task
                    try:
                        async with client.get(f"http://127.0.0.1:{port}") as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("VAS startup failed")
                path = ROOT / "config/memory-pipeline.example.yaml"
                data = yaml.safe_load(path.read_text())
                data["input"] = {"mode": mode}
                scenario = Scenario.from_dict(data, base_dir=path.parent)
                output = tmp_path / mode
                transport = WebSocketTransport(
                    f"ws://127.0.0.1:{port}",
                    device_id="02:00:00:00:00:01",
                    diagnostics="frame",
                    fake_device_tools=True,
                )
                await run_scenario(scenario, transport, output)
                report = create_report(output)
                export_excel([report], output / "evaluation.xlsx")
                assert report["status"] == "passed", report.get("failures")
                async with client.get(f"http://127.0.0.1:{port+1}/records") as response:
                    calls = await response.json()
                # Test the externally observed side effect, not just trace labels.
                assert not any(
                    e.get("event") == "mcp_call_received"
                    and e.get("data", {}).get("name") == "self_music_play"
                    for turn in report["turns"]
                    for e in turn["events"]
                )
                for index, turn in enumerate(report["turns"]):
                    rows = turn["vas_events"]
                    starts = [e for e in rows if e["event"] == "llm_request_started"]
                    memory_start = next(
                        e for e in rows if e["event"] == "memory_request_started"
                    )
                    memory_end = next(
                        e for e in rows if e["event"] == "memory_request_finished"
                    )
                    assert starts[0]["monotonic_ns"] < memory_end["monotonic_ns"]
                    decisions = [
                        e for e in rows if e["event"] == "llm_candidate_adopted"
                    ]
                    assert len(decisions) == 1
                    assert decisions[0]["monotonic_ns"] >= memory_end["monotonic_ns"]
                    result = next(
                        e for e in rows if e["event"] == "memory_lookup_result"
                    )
                    assert result["data"]["retrieval_status"] == (
                        "miss" if index == 0 else "hit"
                    )
                    if index:
                        discard = next(
                            e for e in rows if e["event"] == "llm_candidate_discarded"
                        )
                        assert (
                            discard["data"]["pipeline_attempt_id"]
                            == starts[0]["data"]["pipeline_attempt_id"]
                        )
                        assert (
                            starts[1]["data"]["replaces_attempt_id"]
                            == starts[0]["data"]["pipeline_attempt_id"]
                        )
                        assert not any(
                            e["event"] == "tool_call_started"
                            and e["monotonic_ns"] < decisions[0]["monotonic_ns"]
                            for e in rows
                        )
                assert (output / "evaluation.xlsx").is_file()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
