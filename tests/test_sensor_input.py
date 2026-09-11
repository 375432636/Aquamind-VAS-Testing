"""Sensor steps share the audio session but do not upload synthetic speech."""

import asyncio
import json
import math
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import opuslib_next as opuslib
import pytest
from websockets.asyncio.server import serve

ROOT = Path(__file__).resolve().parents[1]
MODES = ["touch-head", "touch-hand", "shake-body", "throw-it-up"]


@pytest.mark.parametrize(
    "turn",
    [
        {"sensor": "unknown"},
        {"sensor": ""},
        {"sensor": None},
        {"sensor": "touch-head", "text": "摸头"},
        {"sensor": "touch-head", "audio": "fixtures/input.wav"},
    ],
)
def test_invalid_sensor_step_is_rejected_before_connecting(tmp_path, turn):
    outcome = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_scenarios.ci_run",
            "--prepare-only",
            "--output",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=environment([turn], "manual"),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert outcome.returncode == 2
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "failed"
    assert result["turns"] == []
    assert result["error"].startswith("ValueError:")
    assert not list(tmp_path.rglob("*.wav"))


def environment(turns, mode):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VAS_")}
    env.update(
        VAS_ENVIRONMENT="dev",
        VAS_DEVICE_ID="30:ED:A0:A6:23:A4",
        VAS_INPUT_MODE=mode,
        VAS_TURNS_JSON=json.dumps(turns),
    )
    return env


@pytest.mark.parametrize("mode", ["manual", "vad"])
def test_mixed_audio_and_four_sensor_steps_preserve_wire_protocol_and_report(
    tmp_path, mode
):
    turns = [{"audio": "fixtures/input.wav"}]
    turns += [{"sensor": sensor} for sensor in MODES]
    turns += [{"audio": "fixtures/input.wav"}]
    output = tmp_path / mode
    observed, diagnostic_rows = [], []
    with wave.open(str(ROOT / "fixtures/input.wav")) as audio:
        vad_end_frames = 5 + math.ceil(audio.getnframes() / 960) + 2

    async def exercise():
        async def peer(ws):
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            listen_id = 0
            audio_seq = 0
            response = 0
            input_frames = 0
            responded = False

            def event(name, **fields):
                row = dict(
                    schema_version=1,
                    server_session_id="sensor-session",
                    clock_id="vas",
                    seq=len(diagnostic_rows) + 1,
                    monotonic_ns=time.monotonic_ns(),
                    event=name,
                    listen_turn_id=listen_id or None,
                    data={},
                    status="ok",
                )
                row.update(fields)
                diagnostic_rows.append(row)

            async def tts_state(state, **fields):
                await ws.send(json.dumps({"type": "tts", "state": state, **fields}))
                event("tts_state_sent", data={"state": state})

            async def respond(sensor=None, greeting=False):
                nonlocal audio_seq, response
                response += 1
                label = "欢迎" if greeting else f"回应 {sensor or '音频'}"
                if not greeting:
                    if sensor:
                        # The existing VAS abort handler sends this stop outside
                        # send_tts_message before admitting a replacement reply.
                        await ws.send(json.dumps({"type": "tts", "state": "stop"}))
                        event("abort_stop_sent")
                    if not sensor:
                        event("asr_final")
                    # Real VAS echoes the interpreted sensor input through STT, too.
                    await ws.send(
                        json.dumps({"type": "stt", "text": sensor or "音频输入"})
                    )
                await tts_state("start")
                llm = f"llm-{response}"
                tts = f"tts-{response}"
                event("memory_request_started", span_id=f"memory-{response}")
                event("memory_request_finished", span_id=f"memory-{response}")
                event("llm_request_started", span_id=llm)
                event("tts_request_started", span_id=tts, parent_span_id=llm)
                await tts_state("sentence_start", text=label)
                event(
                    "audio_output_started",
                    output_id=f"output-{response}",
                    output_kind="answer",
                    span_id=tts,
                    parent_span_id=llm,
                    data={"audio_seq": audio_seq + 1},
                )
                for _ in range(2):
                    audio_seq += 1
                    await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                event("tts_request_finished", span_id=tts, parent_span_id=llm)
                event("llm_request_finished", span_id=llm)
                await tts_state("stop")

            async for raw in ws:
                if isinstance(raw, bytes):
                    observed.append("audio")
                    input_frames += 1
                    if (
                        mode == "vad"
                        and input_frames >= vad_end_frames
                        and not responded
                    ):
                        responded = True
                        await respond()
                    continue
                message = json.loads(raw)
                observed.append(message)
                kind, state = message["type"], message.get("state")
                if kind == "diagnostics" and state == "start":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "diagnostics",
                                "state": "started",
                                "schema_version": 1,
                                "server_session_id": "sensor-session",
                                "level": "frame",
                            }
                        )
                    )
                elif kind == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "sensor-session",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )
                    await respond(greeting=True)
                elif kind == "listen" and state == "start":
                    listen_id += 1
                    input_frames, responded = 0, False
                    event("listen_start_received")
                elif kind == "listen" and state == "stop":
                    await respond()
                elif kind == "sensor":
                    assert message == {
                        "type": "sensor",
                        "mode": MODES[response - 2],
                        "state": "stop",
                    }
                    await respond(message["mode"])
                elif kind == "diagnostics" and state == "finish":
                    # Delayed diagnostic delivery must not change step ownership.
                    await ws.send(
                        json.dumps(
                            {
                                "type": "diagnostics",
                                "state": "events",
                                "schema_version": 1,
                                "server_session_id": "sensor-session",
                                "events": diagnostic_rows,
                                "complete": True,
                                "finished": True,
                                "next_seq": len(diagnostic_rows),
                                "end_seq": len(diagnostic_rows),
                            }
                        )
                    )
                    return

        async with serve(peer, "127.0.0.1", 0) as server:
            env = environment(turns, mode)
            env["VAS_DEV_URL"] = (
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/looomyn/v1/"
            )
            return await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "voice_scenarios.ci_run",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=25,
            )

    outcome = asyncio.run(exercise())
    assert outcome.returncode == 0, outcome.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "passed", report.get("failures")
    assert len(report["turns"]) == 6
    controls = [x for x in observed if isinstance(x, dict)]
    assert sum(x["type"] == "hello" for x in controls) == 1
    assert (
        sum(x["type"] == "listen" and x.get("state") == "start" for x in controls) == 2
    )
    assert sum(
        x["type"] == "listen" and x.get("state") == "stop" for x in controls
    ) == (2 if mode == "manual" else 0)
    sensor_positions = [
        i
        for i, x in enumerate(observed)
        if isinstance(x, dict) and x["type"] == "sensor"
    ]
    assert len(sensor_positions) == 4
    assert "audio" not in observed[sensor_positions[0] : sensor_positions[-1] + 1]
    for i, turn in enumerate(report["turns"]):
        assert turn["metrics"]["llm_requests"] == 1
        assert turn["metrics"]["tts_requests"] == 1
        assert turn["metrics"]["memory_requests"] == 1
        assert turn["metrics"]["first_playback_ms"] is not None
        if 1 <= i <= 4:
            assert "input" not in turn["audio"] and "uplink" not in turn["audio"]
            assert turn["metrics"]["asr_text"] is None
            assert turn["reply_timing"]["zero_event"] == "sensor_sent"
            assert len(turn["reply_timing"]["sentences"]) == 1
            assert (
                turn["reply_timing"]["sentences"][0]["audio"]["played"]["status"]
                == "ready"
            )
            assert "传感器" in (output / f"turn-{i+1:03d}.html").read_text()
    assert report["session_playback"]["status"] == "ready"
    assert len(list((output / "inputs").glob("*.wav"))) == 2
    from voice_scenarios.report import evaluate

    raw_result = json.loads((output / "result.json").read_text())
    missing_control = [
        e
        for e in diagnostic_rows
        if e["seq"]
        != next(e["seq"] for e in diagnostic_rows if e["event"] == "tts_state_sent")
    ]
    broken = evaluate(raw_result, missing_control)
    assert broken["status"] == "failed"
    assert any("无法可靠关联" in message for message in broken["failures"])


def test_sensor_only_cli_needs_no_input_audio_and_keeps_session_playback(tmp_path):
    received = []

    async def exercise():
        async def peer(ws):
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            async for raw in ws:
                assert isinstance(raw, str)
                message = json.loads(raw)
                received.append(message)
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "session_id": "sensor-only",
                                "audio_params": {
                                    "format": "opus",
                                    "sample_rate": 16000,
                                    "channels": 1,
                                },
                            }
                        )
                    )
                elif message["type"] == "sensor":
                    await ws.send(json.dumps({"type": "stt", "text": "我摸了摸你的头"}))
                    await ws.send(json.dumps({"type": "tts", "state": "start"}))
                    await ws.send(
                        json.dumps(
                            {"type": "tts", "state": "sentence_start", "text": "你好呀"}
                        )
                    )
                    await ws.send(encoder.encode(b"\x00\x10" * 960, 960))
                    await ws.send(json.dumps({"type": "tts", "state": "stop"}))

        async with serve(peer, "127.0.0.1", 0) as server:
            return await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "main.py",
                    "run",
                    "--sensor",
                    "touch-head",
                    "--device-id",
                    "30:ED:A0:A6:23:A4",
                    "--url",
                    f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                    "--output",
                    str(tmp_path / "run"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )

    outcome = asyncio.run(exercise())
    assert outcome.returncode == 0, outcome.stderr
    assert received[-1] == {"type": "sensor", "mode": "touch-head", "state": "stop"}
    assert not any(m["type"] == "listen" for m in received)
    report = json.loads((tmp_path / "run/report.json").read_text())
    assert report["session_playback"]["status"] == "ready"
    assert report["turns"][0]["reply_timing"]["zero_event"] == "sensor_sent"
    page = (tmp_path / "run/turn-001.html").read_text()
    assert "手动停止</span>" not in page
    assert "传感器" in page
