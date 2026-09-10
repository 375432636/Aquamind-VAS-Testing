"""Opt-in regression against real VAS providers; no business-method mocks."""

import asyncio
import hashlib
import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

from voice_scenarios.__main__ import run
from voice_scenarios.dev_stack import ROOT, serve_stack


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
            assert await run(args) == 0
            import json

            report = json.loads((tmp_path / "report/report.json").read_text())
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
            assert len(report["turns"]) == 5
            assert report["turns"][2]["interruption"]["tts_abort_observed"]
            assert report["turns"][3]["metrics"]["tools"] == {"self_music_play": 1}
            assert report["turns"][4]["metrics"]["tools"] == {"self_music_stop": 1}
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
