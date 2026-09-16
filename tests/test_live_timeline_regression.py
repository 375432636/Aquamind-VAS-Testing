"""Offline acceptance: browser journal -> three-turn HTML/Excel/audio report.

Synthetic PCM and clocks only: no microphone, TTS download, or VAS connection.
The second turn can reproduce the legacy clock fault or an uncertain tail.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import wave
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from voice_scenarios.__main__ import create_report
from voice_scenarios.interactive_session import InteractiveSession

BASE = 100_000_000_000
WALL = 1_700_000_000_000_000_000
RATE = 16000
SAMPLES = 1600


def pcm(value):
    return base64.b64encode(struct.pack(f"<{SAMPLES}h", *([value] * SAMPLES))).decode()


async def record_session(directory, clock_mode):
    session = InteractiveSession(
        directory, {"environment": "dev", "device_id": "00:00:00:00:00:21"}
    )
    session.result["name"] = "客户端时间轴回归 · 三轮 PTT"
    session.result["data_source"] = "mock"
    edges = []

    def emit(name, seconds, turn=0, **data):
        session.accept(
            dict(
                event=name,
                at_ns=BASE + round(seconds * 1e9),
                wall_time_ns=WALL + round(seconds * 1e9),
                turn=turn,
                data=data,
            )
        )

    def reply(turn, seq, received, played, kind, value, text, reliable=True):
        emit("tts_sentence_start", received, turn, text=text)
        emit("audio_packet_received", received, turn, audio_seq=seq)
        emit(
            "audio_received",
            received,
            turn,
            audio_seq=seq,
            pcm=pcm(value),
            sample_rate=RATE,
        )
        observed = max(received, played) + 0.2
        timing = dict(
            timing_model="browser_audio_context_v2",
            timing_method="output_timestamp" if reliable else "context_clock_estimate",
            output_start_confirmed=reliable,
            output_end_confirmed=reliable,
            playback_observed_at_ns=BASE + round(observed * 1e9),
        )
        if clock_mode == "legacy" and turn == 2:
            timing = dict(timing_model="browser_audio_context_v1")
        emit("playback_started", played, turn)
        emit(
            "playback_frame_started",
            played,
            turn,
            audio_seq=seq,
            pcm=pcm(value),
            sample_rate=RATE,
            **timing,
        )
        edges.append(
            dict(
                event="audio_output_started",
                server_session_id="clock-regression",
                seq=len(edges) + 1,
                listen_turn_id=turn,
                clock_id="independent-server",
                monotonic_ns=900_000_000_000 + round(received * 1e9),
                # Server wall clock is deliberately seven seconds ahead. Never
                # shift client playback to make it line up with these events.
                wall_time_ns=WALL + round((received + 7) * 1e9),
                output_kind=kind,
                data={"audio_seq": seq},
            )
        )

    emit("connection_started", 0)
    emit("hello_received", 0.1, session_id="clock-regression")
    for turn, start in enumerate((1, 11, 21), 1):
        emit("turn_started", start, turn, mode="manual", text=f"测试问题 {turn}")
        emit("listen_start_sent", start, turn, mode="manual")
        emit("input_started", start, turn)
        emit(
            "input_audio_frame_sent",
            start,
            turn,
            pcm=pcm(100 * turn),
            sample_rate=RATE,
            pcm_offset_samples=0,
            stream="input",
            is_speech=True,
        )
        emit("listen_stop_sent", start + 1, turn)
        if turn == 1:
            reply(1, 1, 3, 3.25, "pre_speech", 1000, "我来查一下。")
            reply(1, 2, 4.6, 4.85, "answer", 2000, "这是正式回复。")
            emit("tts_stop", 5, 1)
            emit("playback_drained", 5.1, 1)
            emit("turn_finished", 5.1, 1)
        elif turn == 2:
            played = 10 if clock_mode == "legacy" else 14.5
            reply(2, 3, 14, played, "answer", 3000, "这是第二轮回复。")
            if clock_mode == "partial":
                # Appended later, but a bad estimate sorts before the good frame.
                reply(2, 4, 14.55, 10, "answer", 4000, "未确认的尾部。", False)
                emit("playback_stopped", 14.8, 2)
            else:
                emit("tts_stop", 14.8, 2)
                emit("playback_drained", 14.9, 2)
            emit("turn_finished", 15, 2, interrupted=clock_mode == "partial")
        else:
            emit("abort_sent", 24.25, 3)
            emit("playback_stopped", 24.3, 3)
            emit("turn_finished", 24.5, 3, interrupted=True)
    emit(
        "diagnostics",
        25,
        schema_version=1,
        server_session_id="clock-regression",
        events=edges,
        next_seq=len(edges),
        end_seq=len(edges),
        complete=True,
        finished=True,
    )
    return await session.finish()


def html_data(path):
    match = re.search(
        r'<script id="data" type="application/json">(.*?)</script>',
        path.read_text(),
        re.S,
    )
    assert match, "Static report must embed its data for offline use"
    return json.loads(match[1])


def signature(paths):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@pytest.mark.parametrize("clock_mode", ["valid", "legacy", "partial"])
def test_three_turn_recording_preserves_actual_waits_through_export_and_reload(
    tmp_path, clock_mode
):
    report = asyncio.run(record_session(tmp_path, clock_mode))
    playback = report["session_playback"]
    assert playback["zero_at_ns"] == BASE
    expected_waits = [
        (1, "first_reply", 2, 3.25),
        (1, "transition", 3.35, 4.85),
        (
            2,
            "first_packet" if clock_mode == "legacy" else "first_reply",
            12,
            14 if clock_mode == "legacy" else 14.5,
        ),
        (3, "interrupted", 22, 24.25),
    ]
    actual_waits = [
        (w["turn_index"], w["kind"], w["start_seconds"], w["end_seconds"])
        for w in playback["waits"]
    ]
    assert actual_waits == expected_waits
    assert report["turns"][0]["metrics"]["first_playback_ms"] == 1250
    assert report["turns"][1]["metrics"]["first_playback_ms"] == (
        None if clock_mode == "legacy" else 2500
    )
    assert playback["turns"][1]["playback_clock"]["status"] == (
        "invalid" if clock_mode == "legacy" else clock_mode
    )

    overview = html_data(tmp_path / "report.html")["session_playback"]
    assert overview["waits"] == playback["waits"]
    for index in range(1, 4):
        page = html_data(tmp_path / f"turn-{index:03}.html")["session_playback"]
        assert page["zero_at_ns"] == BASE
        assert page["waits"] == [
            w for w in playback["waits"] if w["turn_index"] == index
        ]
        assert [
            (m["kind"], m["at_seconds"])
            for m in page["markers"]
            if m["kind"] in {"ptt_start", "ptt_stop"}
        ] == [
            ("ptt_start", (index - 1) * 10 + 1),
            ("ptt_stop", (index - 1) * 10 + 2),
        ]

    # Verify the audio product too, including real silence between replies.
    with wave.open(str(tmp_path / playback["path"])) as audio:
        samples = struct.unpack(
            f"<{audio.getnframes() * 2}h", audio.readframes(audio.getnframes())
        )
    right = samples[1::2]
    assert right[52000:53600] == (1000,) * SAMPLES
    assert right[53600:77600] == (0,) * 24000
    assert right[77600:79200] == (2000,) * SAMPLES
    assert right[232000:233600] == ((0 if clock_mode == "legacy" else 3000),) * SAMPLES
    assert 4000 not in right
    if clock_mode != "valid":
        assert 'src="turn-002.played.wav"' in (tmp_path / "turn-002.html").read_text()

    with ZipFile(tmp_path / "evaluation.xlsx") as workbook:
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    assert float(sheet.find(".//s:c[@r='G7']/s:v", ns).text) == 1.25
    assert float(sheet.find(".//s:c[@r='H7']/s:v", ns).text) == 1.5
    second = sheet.find(".//s:c[@r='G8']/s:v", ns)
    assert second is None if clock_mode == "legacy" else float(second.text) == 2.5

    raw = [
        tmp_path / name
        for name in ("result.json", "client-events.jsonl", "vas-events.jsonl")
    ]
    raw.extend(tmp_path.glob("turn-*.wav"))
    original = signature(raw)
    regenerated = create_report(tmp_path)
    assert regenerated["session_playback"] == playback
    assert signature(raw) == original
    assert html_data(tmp_path / "report.html")["session_playback"] == overview
    if clock_mode == "valid" and os.getenv("CI_TIMELINE_REPORT_DIR"):
        shutil.copytree(
            tmp_path, Path(os.environ["CI_TIMELINE_REPORT_DIR"]), dirs_exist_ok=True
        )
