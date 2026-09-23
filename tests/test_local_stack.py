"""Opt-in regression against real VAS providers; no business-method mocks."""

import asyncio
import hashlib
import json
import os
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from voice_scenarios.__main__ import run
from voice_scenarios.dev_stack import ROOT, serve_stack


@pytest.mark.skipif(
    not os.getenv("VAS_TEST_ROOT"), reason="Set VAS_TEST_ROOT for real VAS"
)
def test_real_stack_public_milestones_and_guardrail(tmp_path):
    async def exercise():
        import aiohttp

        task = asyncio.create_task(
            serve_stack(
                Path(os.environ["VAS_TEST_ROOT"]),
                ROOT / "config/fake-milestones.yaml",
                19150,
            )
        )
        try:
            async with aiohttp.ClientSession() as client:
                for _ in range(250):
                    if task.done():
                        await task
                    try:
                        async with client.get("http://127.0.0.1:19150") as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("VAS did not start")
            args = Namespace(
                config=ROOT / "config/local-fake.example.yaml",
                url="ws://127.0.0.1:19150",
                diagnostics="stage",
                scenario=ROOT / "config/milestones.example.yaml",
                audio=None,
                output=tmp_path / "milestones",
                repeat=1,
                device_id=None,
                fake_device=True,
            )
            assert await run(args) == 0
            report = json.loads((args.output / "report.json").read_text())
            assert report["diagnostics"]["complete"]
            assert report["clock_sync"]["status"] == "calibrated"
            assert {sample["phase"] for sample in report["clock_sync"]["samples"]} == {
                "start",
                "end",
            }
            for turn in report["turns"]:
                events = turn["vas_events"]
                names = {e["event"] for e in events}
                assert {
                    "llm_first_token",
                    "llm_first_sse",
                    "llm_first_output",
                    "llm_usage",
                    "tts_first_text",
                    "tts_segment_ready",
                    "http_request_body_sent",
                    "http_response_headers",
                    "tts_first_pcm",
                    "guardrail_embedding_finished",
                    "guardrail_released",
                    "asr_final",
                } <= names
                assert turn["llm_requests"]
                for attempt in turn["llm_requests"]:
                    assert attempt["http_request_id"]
                    assert attempt["connection_state"] in {"new", "reused"}
                    assert attempt["sent_to_headers_ms"] is not None
                    assert attempt["first_sse_to_output_ms"] is not None
                    # Fake streaming responses omit usage; zero would be fabricated.
                    assert attempt["input_tokens"] is None
                    assert attempt["cached_tokens"] is None
                assert len({s["span_id"] for s in turn["spans"]}) == len(turn["spans"])
                for request in (
                    e for e in events if e["event"] == "tts_request_started"
                ):
                    related = [e for e in events if e["span_id"] == request["span_id"]]
                    headers = next(
                        e for e in related if e["event"] == "http_response_headers"
                    )
                    pcm = next(e for e in related if e["event"] == "tts_first_pcm")
                    assert pcm["monotonic_ns"] >= headers["monotonic_ns"]
                    assert request["data"]["model"] == "fake-speech"
            all_names = {e["event"] for t in report["turns"] for e in t["vas_events"]}
            assert "http_connection_reused" in all_names
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("VAS_TEST_ROOT"), reason="Set VAS_TEST_ROOT for real VAS"
)
def test_real_stack_vad_continuous_noise_and_multiturn(tmp_path):
    async def exercise():
        import aiohttp

        task = asyncio.create_task(
            serve_stack(
                Path(os.environ["VAS_TEST_ROOT"]), ROOT / "config/fake-vad.yaml", 19120
            )
        )
        try:
            async with aiohttp.ClientSession() as client:
                for _ in range(250):
                    if task.done():
                        await task
                    try:
                        async with client.get("http://127.0.0.1:19120") as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("VAS did not start")
                output = tmp_path / "vad"
                args = Namespace(
                    config=ROOT / "config/local-fake.example.yaml",
                    url="ws://127.0.0.1:19120",
                    diagnostics="stage",
                    scenario=ROOT / "config/vad.example.yaml",
                    audio=None,
                    output=output,
                    repeat=1,
                    device_id=None,
                    fake_device=True,
                )
                assert await run(args) == 0
                report = json.loads((output / "report.json").read_text())
                assert len(report["turns"]) == 2
                assert report["diagnostics"]["complete"]
                assert report["clock_sync"]["status"] == "calibrated"
                for index, turn in enumerate(report["turns"], 1):
                    assert turn["status"] == "completed"
                    assert turn["metrics"]["asr_endpoint_to_final_ms"] >= 0
                    assert turn["metrics"]["asr_commit_to_final_ms"] is None
                    assert not any(
                        e["event"] == "listen_stop_sent" for e in turn["events"]
                    )
                    assert {e["listen_turn_id"] for e in turn["vas_events"]} == {index}
                    import wave

                    with (
                        wave.open(turn["audio"]["uplink"]) as up,
                        wave.open(turn["audio"]["input"]) as src,
                    ):
                        assert up.getnframes() > src.getnframes() + 16000
                async with client.get("http://127.0.0.1:19121/records") as response:
                    records = await response.json()
                assert (
                    len([r for r in records if r.get("operation") == "vad_endpoint"])
                    == 2
                )
                assert not any(r.get("operation") == "commit" for r in records)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("VAS_TEST_ROOT"),
    reason="Set VAS_TEST_ROOT to run the real local stack",
)
def test_real_stack_multiturn_tools_interruption_and_music(tmp_path):
    async def exercise():
        import aiohttp

        task = asyncio.create_task(
            serve_stack(
                Path(os.environ["VAS_TEST_ROOT"]),
                ROOT / "config/fake-regression.yaml",
                19090,
            )
        )
        try:
            async with aiohttp.ClientSession() as client:
                for _ in range(250):
                    if task.done():
                        await task
                    try:
                        async with client.get("http://127.0.0.1:19090") as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("VAS did not start")
            args = Namespace(
                config=ROOT / "config/local-fake.example.yaml",
                url="ws://127.0.0.1:19090",
                diagnostics=None,
                scenario=ROOT / "config/regression.example.yaml",
                audio=None,
                output=tmp_path / "report",
                repeat=1,
                device_id=None,
                fake_device=True,
            )
            exit_code = await run(args)
            async with aiohttp.ClientSession() as client:
                async with client.get("http://127.0.0.1:19091/records") as response:
                    response.raise_for_status()
                    records = await response.json()
            (tmp_path / "fake-records.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2)
            )
            report = json.loads((tmp_path / "report/report.json").read_text())
            assert exit_code == 0, report.get("failures")
            assert report["diagnostics"]["complete"]
            assert report["diagnostics"]["transport"] == "websocket"
            writebacks = {
                e["span_id"]
                for e in report["session_events"]
                if e["event"] == "memory_request_started"
                and e["data"].get("operation") == "update_memory"
            }
            assert writebacks
            assert any(
                e["event"] == "memory_request_finished"
                and e["span_id"] in writebacks
                and e["status"] == "ok"
                for e in report["session_events"]
            )
            assert len(report["turns"]) == 6
            script = yaml.safe_load((ROOT / "config/fake-regression.yaml").read_text())
            reads = [
                record
                for record in records
                if record["service"] == "memory" and record["operation"] == "get_memory"
            ]
            # Independently verify real HTTP calls, including the same question
            # asked again in a new turn. Diagnostic counts alone could miss calls.
            assert [r["query"] for r in reads] == [t["text"] for t in script["turns"]]
            assert reads[1]["query"] == reads[2]["query"]
            for turn, hit_count in zip(
                report["turns"], (0, 2, 2, 0, 1, 1), strict=True
            ):
                events = turn["vas_events"]
                requests = [
                    e
                    for e in events
                    if e["event"] == "memory_request_started"
                    and e["data"].get("operation") == "get_memory"
                ]
                assert len(requests) == 1, turn["id"]
                assert any(
                    e["event"] == "memory_request_finished"
                    and e["span_id"] == requests[0]["span_id"]
                    and e["status"] == "ok"
                    for e in events
                ), turn["id"]
                hits = [
                    e
                    for e in events
                    if e["event"] == "memory_cache_hit"
                    and e["data"].get("cache") == "turn_memory"
                ]
                assert len(hits) == hit_count, turn["id"]
            by_id = {turn["id"]: turn for turn in report["turns"]}
            assert by_id["interrupt-answer"]["interruption"]["tts_abort_observed"]
            assert by_id["music"]["metrics"]["tools"] == {"self_music_play": 1}
            assert by_id["stop-music"]["metrics"]["tools"] == {"self_music_stop": 1}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("VAS_TEST_ROOT"), reason="Set VAS_TEST_ROOT for real VAS"
)
def test_real_stack_diagnostic_modes_preserve_received_audio(tmp_path):
    """Compare actual decoded output through independent, identical Fake stacks."""

    async def exercise():
        import aiohttp

        fingerprints = []
        for level in ("off", "stage", "frame"):
            task = asyncio.create_task(
                serve_stack(
                    Path(os.environ["VAS_TEST_ROOT"]),
                    ROOT / "config/fake-regression.yaml",
                    19110,
                )
            )
            try:
                async with aiohttp.ClientSession() as client:
                    for _ in range(250):
                        if task.done():
                            await task
                        try:
                            async with client.get("http://127.0.0.1:19110") as response:
                                if response.status == 200:
                                    break
                        except aiohttp.ClientError:
                            pass
                        await asyncio.sleep(0.1)
                    else:
                        raise TimeoutError("VAS did not start")
                output = tmp_path / level
                args = Namespace(
                    config=ROOT / "config/local-fake.example.yaml",
                    url="ws://127.0.0.1:19110",
                    diagnostics=level,
                    scenario=None,
                    audio=ROOT / "fixtures/input.wav",
                    output=output,
                    repeat=1,
                    device_id=None,
                    fake_device=True,
                )
                assert await run(args) == 0
                report = json.loads((output / "report.json").read_text())
                if level != "off":
                    assert report["diagnostics"]["complete"]
                fingerprints.append(
                    hashlib.sha256(
                        (output / "turn-001.received.wav").read_bytes()
                    ).hexdigest()
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert len(set(fingerprints)) == 1

    asyncio.run(exercise())
