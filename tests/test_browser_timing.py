"""Browser reports preserve recorded waits without trusting broken audio clocks."""

import copy
import struct
import wave

import pytest

from voice_scenarios.clock_timeline import combined_timeline
from voice_scenarios.reply_audio import prepare_reply_audio
from voice_scenarios.reply_timing import analyze_reply_timing
from voice_scenarios.report import build_report, evaluate
from voice_scenarios.session_timing import prepare_session_playback

BASE = 100_000_000_000


def event(name, seconds, **data):
    return {"event": name, "at_ns": BASE + round(seconds * 1e9), "data": data}


def packet(seconds, offset=0, speech=True):
    return event(
        "input_audio_frame_sent",
        seconds,
        pcm_offset_samples=offset,
        samples=1600,
        sample_rate=16000,
        stream="input",
        is_speech=speech,
        listen_turn_id=1,
    )


def turn(tmp_path, extra=(), input_events=None):
    input_events = input_events or [event("input_started", 1), packet(1)]
    paths = {}
    count = sum(e["event"] == "input_audio_frame_sent" for e in input_events)
    for kind, samples in (
        ("input", count * 1600),
        ("played", 1600),
        ("received", 1600),
    ):
        path = tmp_path / f"turn-001.{kind}.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setparams((1, 2, 16000, 0, "NONE", ""))
            writer.writeframes(struct.pack(f"<{samples}h", *([1000] * samples)))
        paths[kind] = path.name
    return {
        "id": "turn-001",
        "listen_turn_id": 1,
        "input_type": "audio",
        "status": "interrupted",
        "input_settings": {"mode": "manual"},
        "playback_source": "browser_audio_context",
        "audio": paths,
        "events": [
            event("turn_started", 0.9),
            *input_events,
            event("listen_stop_sent", 2),
            *extra,
        ],
    }


def response(playback=1.5):
    return [
        event("tts_sentence_start", 3),
        event("audio_packet_received", 3, audio_seq=8),
        event(
            "audio_received",
            3,
            audio_seq=8,
            bytes=3200,
            sample_rate=16000,
            duration_ms=100,
            output_kind="answer",
        ),
        event("playback_started", playback),
        event("playback_frame_started", playback, audio_seq=8),
        event("tts_stop", 4),
    ]


def session(item, tmp_path, **extra):
    return prepare_session_playback(
        {"turns": [item], "connection_started_at_ns": BASE, **extra}, tmp_path
    )


def test_invalid_browser_clock_keeps_first_packet_wait_and_raw_audio(tmp_path):
    item = turn(tmp_path, response())
    original = copy.deepcopy(item)
    playback = session(item, tmp_path)
    row = playback["turns"][0]
    assert row["first_playback_seconds"] is None
    assert row["first_received_seconds"] == 3
    assert row["playback_clock"]["status"] == "invalid"
    assert row["unaligned_audio"]["played"] == "turn-001.played.wav"
    assert playback["waits"] == [
        {
            "turn_index": 1,
            "kind": "first_packet",
            "start_seconds": 2,
            "end_seconds": 3,
            "duration_seconds": 1,
        }
    ]
    assert playback["segments"][0]["start_seconds"] is None
    assert any(x["code"] == "invalid_playback_clock" for x in playback["limitations"])
    with wave.open(str(tmp_path / playback["path"])) as source:
        values = struct.unpack(
            f"<{source.getnframes() * 2}h", source.readframes(source.getnframes())
        )
    assert not any(values[1::2])
    assert item == original


def test_invalid_browser_clock_cannot_pass_latency_or_sentence_timing(tmp_path):
    item = turn(tmp_path, response())
    item["expected"] = {"max_first_answer_playback_ms": 2000}
    vas = [
        {
            "event": "audio_output_started",
            "listen_turn_id": 1,
            "monotonic_ns": 1,
            "output_kind": "answer",
            "data": {"audio_seq": 8},
        }
    ]
    report = evaluate({"name": "browser", "status": "passed", "turns": [item]}, vas)
    reported = report["turns"][0]
    assert reported["metrics"]["first_playback_ms"] is None
    assert reported["metrics"]["first_answer_playback_ms"] is None
    assert reported["checks"][0]["status"] == "unknown"
    timing = analyze_reply_timing(item)
    assert timing["sentences"][0]["start_seconds"] is None
    assert timing["sentences"][0]["status"] == "timing_unavailable"


@pytest.mark.parametrize(
    "ending,kind,end",
    [
        (
            [
                event("abort_sent", 4),
                event("playback_stopped", 4.1),
                event("turn_finished", 5),
            ],
            "interrupted",
            4,
        ),
        (
            [event("playback_stopped", 4.1), event("turn_finished", 5)],
            "interrupted",
            4.1,
        ),
        ([event("turn_finished", 5)], "no_reply", 5),
    ],
)
def test_no_reply_wait_ends_at_actual_interruption_or_last_client_event(
    tmp_path, ending, kind, end
):
    playback = session(turn(tmp_path, ending), tmp_path)
    assert playback["waits"] == [
        {
            "turn_index": 1,
            "kind": kind,
            "start_seconds": 2,
            "end_seconds": end,
            "duration_seconds": end - 2,
        }
    ]


def test_new_browser_clock_uses_valid_playback_instead_of_first_packet(tmp_path):
    item = turn(tmp_path, response(3.25))
    playback = session(item, tmp_path)
    assert playback["turns"][0]["first_playback_seconds"] == 3.25
    assert playback["waits"][0]["kind"] == "first_reply"
    assert playback["waits"][0]["duration_seconds"] == 1.25
    assert analyze_reply_timing(item)["sentences"][0]["start_seconds"] == 1.25


def test_overlapping_input_keeps_actual_send_boundaries_separate_from_wav_queue(
    tmp_path,
):
    item = turn(
        tmp_path,
        input_events=[event("input_started", 1), packet(1), packet(1.05, 1600, False)],
    )
    playback = session(item, tmp_path)
    row = playback["turns"][0]
    assert row["input_start_seconds"] == 1
    speech, background = row["input_segments"]
    assert speech["end_seconds"] == 1.1
    assert background["start_seconds"] == 1.05
    assert background["end_seconds"] == 1.15
    assert background["replay_start_seconds"] == 1.1
    assert background["replay_end_seconds"] == 1.2
    assert any(
        m["kind"] == "input_started" and m["at_seconds"] == 1
        for m in playback["markers"]
    )


def test_input_frames_can_anchor_session_without_first_audio_sent(tmp_path):
    item = turn(tmp_path, input_events=[packet(1)])
    playback = prepare_session_playback({"turns": [item]}, tmp_path)
    assert playback["zero_at_ns"] == BASE + 1_000_000_000
    assert playback["turns"][0]["input_start_seconds"] == 0


def test_invalid_greeting_clock_does_not_place_audio_before_connection(tmp_path):
    item = turn(tmp_path, response(-1))
    playback = session(turn(tmp_path), tmp_path, startup={**item, "received_frames": 1})
    assert all(
        s["start_seconds"] is None or s["start_seconds"] >= 0
        for s in playback["segments"]
    )
    assert any(
        x["code"] == "invalid_playback_clock" and x["turn_index"] == 0
        for x in playback["limitations"]
    )


def test_unconfirmed_context_clock_is_not_reported_as_actual_first_sound(tmp_path):
    item = turn(tmp_path, response(3.25))
    frame = next(e for e in item["events"] if e["event"] == "playback_frame_started")
    frame["data"].update(
        timing_model="browser_audio_context_v2", timing_method="context_clock_estimate"
    )
    playback = session(item, tmp_path)
    assert playback["turns"][0]["playback_clock"]["status"] == "estimated"
    assert playback["turns"][0]["first_playback_seconds"] is None
    assert playback["waits"][0]["kind"] == "first_packet"
    assert analyze_reply_timing(item)["sentences"][0]["start_seconds"] is None


def test_invalid_clock_is_explained_and_original_reply_can_be_heard(tmp_path):
    item = turn(tmp_path, response())
    report = evaluate({"name": "browser", "status": "passed", "turns": [item]}, [])
    prepare_session_playback(report, tmp_path)
    build_report(report, tmp_path / "report.html")
    overview = (tmp_path / "report.html").read_text()
    page = (tmp_path / "turn-001.html").read_text()
    assert "首包等待不等于首音等待" in overview
    assert "第 1 轮" in overview
    assert "整段回听未包含无法对齐的回复" in overview
    assert 'src="turn-001.played.wav"' in page
    assert "原始回复回听（未对齐）" in page
    assert "回复按客户端播放记录还原" not in overview
    chart = combined_timeline(item, {"monotonic_ns": BASE, "wall_time_ns": BASE})
    assert not any(
        m["event"] == "playback_started"
        for lane in chart["lanes"]
        for m in lane["markers"]
    )


def test_reply_already_started_before_input_end_is_not_labeled_no_reply(tmp_path):
    events = response(3.25)
    for row in events:
        row["at_ns"] -= 2_000_000_000
    events.append(event("turn_finished", 4))
    playback = session(turn(tmp_path, events), tmp_path)
    assert playback["turns"][0]["first_playback_seconds"] == 1.25
    assert playback["waits"] == []


def confirmed_data(observed, confirmed=True):
    return {
        "timing_model": "browser_audio_context_v2",
        "timing_method": "output_timestamp" if confirmed else "context_clock_estimate",
        "output_start_confirmed": confirmed,
        "output_end_confirmed": confirmed,
        "playback_observed_at_ns": BASE + round(observed * 1e9),
    }


@pytest.mark.parametrize("tail_at", [3.35, 1.5])
def test_confirmed_prefix_survives_estimated_tail_and_retains_original_pcm_order(
    tmp_path, tail_at
):
    events = response(3.25)
    events = [e for e in events if e["event"] != "tts_stop"]
    next(e for e in events if e["event"] == "playback_frame_started")["data"].update(
        confirmed_data(3.4)
    )
    events.extend(
        [
            event("audio_packet_received", 3.1, audio_seq=9),
            event(
                "audio_received",
                3.1,
                audio_seq=9,
                bytes=3200,
                sample_rate=16000,
                duration_ms=100,
                output_kind="answer",
            ),
            event(
                "playback_frame_started",
                tail_at,
                audio_seq=9,
                **confirmed_data(3.5, False),
            ),
            event("playback_stopped", 3.5),
        ]
    )
    item = turn(tmp_path, sorted(events, key=lambda e: e["at_ns"]))
    # WAV was appended in observation order; finish() subsequently sorted events.
    with wave.open(str(tmp_path / item["audio"]["played"]), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", ""))
        writer.writeframes(struct.pack("<3200h", *([1000] * 1600 + [2000] * 1600)))
    vas = [
        {
            "event": "audio_output_started",
            "listen_turn_id": 1,
            "monotonic_ns": 1,
            "output_kind": "answer",
            "data": {"audio_seq": 8},
        }
    ]
    report = evaluate(
        {
            "name": "browser",
            "status": "passed",
            "turns": [item],
            "connection_started_at_ns": BASE,
        },
        vas,
    )
    playback = prepare_session_playback(report, tmp_path)
    assert playback["turns"][0]["playback_clock"]["status"] == "partial"
    assert playback["turns"][0]["playback_clock"]["trusted_frame_count"] == 1
    assert playback["turns"][0]["first_playback_seconds"] == 3.25
    assert playback["waits"][0]["kind"] == "first_reply"
    assert report["turns"][0]["metrics"]["first_playback_ms"] == 1250
    assert report["turns"][0]["metrics"]["first_answer_playback_ms"] == 1250
    assert playback["segments"][0]["end_seconds"] == 3.35
    assert playback["turns"][0]["unaligned_audio"]["played"] == item["audio"]["played"]
    assert any(x["code"] == "partial_playback_clock" for x in playback["limitations"])
    with wave.open(str(tmp_path / playback["path"])) as source:
        values = struct.unpack(
            f"<{source.getnframes() * 2}h", source.readframes(source.getnframes())
        )
    assert values[1::2][52000:53600] == (1000,) * 1600
    assert 2000 not in values[1::2]
    timing = analyze_reply_timing(item)
    assert timing["sentences"][0]["start_seconds"] == 1.25
    assert timing["sentences"][0]["played_frames"] == 1
    prepare_reply_audio(report, tmp_path)
    clip = report["turns"][0]["reply_timing"]["sentences"][0]["audio"]["played"]
    with wave.open(str(tmp_path / clip["path"])) as source:
        assert (
            struct.unpack("<3200h", source.readframes(3200))
            == (1000,) * 1600 + (2000,) * 1600
        )
    greeting = prepare_session_playback(
        {
            "turns": [],
            "connection_started_at_ns": BASE,
            "startup": {**item, "received_frames": 2},
        },
        tmp_path,
    )
    assert greeting["status"] == "incomplete"
    assert greeting["startup_unaligned_audio"]["played"] == item["audio"]["played"]
    assert any(
        x["code"] == "partial_playback_clock" and x["turn_index"] == 0
        for x in greeting["limitations"]
    )


@pytest.mark.parametrize(
    "first_data,first_at,expected",
    [
        (confirmed_data(3.4, False), 3.25, "partial"),
        ({"timing_model": "browser_audio_context_v1"}, 1.5, "invalid"),
    ],
)
def test_later_confirmed_frame_cannot_replace_unreliable_first_frame(
    tmp_path, first_data, first_at, expected
):
    events = response(first_at)
    next(e for e in events if e["event"] == "playback_frame_started")["data"].update(
        first_data
    )
    events.extend(
        [
            event("audio_packet_received", 3.1, audio_seq=9),
            event(
                "audio_received",
                3.1,
                audio_seq=9,
                bytes=3200,
                sample_rate=16000,
                duration_ms=100,
            ),
            event("playback_frame_started", 3.35, audio_seq=9, **confirmed_data(3.5)),
        ]
    )
    playback = session(turn(tmp_path, events), tmp_path)
    assert playback["turns"][0]["playback_clock"]["status"] == expected
    assert playback["turns"][0]["first_playback_seconds"] is None
    assert playback["waits"][0]["kind"] == "first_packet"
