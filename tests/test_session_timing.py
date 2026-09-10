"""Session replay uses recorded client clocks without collapsing silent gaps."""

import struct
import wave

import pytest

from voice_scenarios.session_timing import prepare_session_playback

BASE = 100_000_000_000
RATE = 16000


def event(name, seconds, **data):
    return {"event": name, "at_ns": BASE + round(seconds * 1e9), "data": data}


def wav(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", ""))
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def frame(seq, seconds, kind="answer", samples=1600):
    return [
        event("audio_packet_received", seconds, audio_seq=seq),
        event(
            "audio_received",
            seconds,
            audio_seq=seq,
            bytes=samples * 2,
            sample_rate=RATE,
            duration_ms=samples / RATE * 1000,
            output_kind=kind,
        ),
    ]


def turn(tmp_path, index, events, input_pcm, reply_pcm, **extra):
    paths = {}
    for kind, values in (("input", input_pcm), ("played", reply_pcm)):
        path = tmp_path / f"turn-{index:03d}.{kind}.wav"
        wav(path, values)
        paths[kind] = str(path)
    return {
        "id": f"turn-{index}",
        "listen_turn_id": index,
        "input_text": f"第 {index} 个问题",
        "status": "completed",
        "events": events,
        "audio": paths,
        **extra,
    }


def samples(directory, metadata):
    with wave.open(str(directory / metadata["path"])) as source:
        assert source.getparams()[:3] == (2, 2, RATE)
        pcm = source.readframes(source.getnframes())
    values = struct.unpack(f"<{len(pcm)//2}h", pcm)
    return values[0::2], values[1::2]


def test_whole_session_preserves_packet_gaps_waits_and_inter_turn_pauses(tmp_path):
    first = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event(
                "input_audio_frame_sent",
                0,
                pcm_offset_samples=0,
                samples=1600,
                sample_rate=RATE,
                stream="input",
                is_speech=True,
                listen_turn_id=1,
            ),
            event(
                "input_audio_frame_sent",
                0.3,
                pcm_offset_samples=1600,
                samples=1600,
                sample_rate=RATE,
                stream="input",
                is_speech=True,
                listen_turn_id=1,
            ),
            event("listen_stop_sent", 0.4),
            event("tts_sentence_start", 1, text="让我查一下"),
            *frame(111, 1, "pre_speech"),
            event("playback_frame_started", 1.2, audio_seq=111),
            # The next sentence control arrives before the older queued packet plays.
            *frame(112, 1.21, "pre_speech"),
            event("tts_sentence_start", 1.3, text="找到了"),
            *frame(113, 1.4),
            event("playback_frame_started", 1.5, audio_seq=112),
            event("playback_frame_started", 2.0, audio_seq=113),
            event("tts_stop", 2.2),
        ],
        [100] * 1600 + [200] * 1600,
        [1000] * 1600 + [2000] * 1600 + [3000] * 1600,
        ended_at_ns=BASE + 2_500_000_000,
    )
    second = turn(
        tmp_path,
        2,
        [
            event("first_audio_sent", 4),
            event(
                "input_audio_frame_sent",
                4,
                pcm_offset_samples=0,
                samples=1600,
                sample_rate=RATE,
                stream="input",
                is_speech=True,
                listen_turn_id=2,
            ),
            event("listen_stop_sent", 4.1),
            event("tts_sentence_start", 5, text="记得上文"),
            *frame(114, 5),
            event("playback_frame_started", 5.2, audio_seq=114),
            event("tts_stop", 5.3),
        ],
        [400] * 1600,
        [4000] * 1600,
        ended_at_ns=BASE + 5_500_000_000,
    )
    report = {"turns": [first, second]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    assert playback["status"] == "ready"
    assert playback["duration_seconds"] == 5.5
    assert playback["zero_at_ns"] == BASE
    left, right = samples(tmp_path, playback)
    assert left[:1600] == (100,) * 1600
    assert left[1600:4800] == (0,) * 3200
    assert left[4800:6400] == (200,) * 1600
    assert left[4 * RATE : 4 * RATE + 1600] == (400,) * 1600
    assert right[round(1.2 * RATE) : round(1.3 * RATE)] == (1000,) * 1600
    assert right[round(1.3 * RATE) : round(1.5 * RATE)] == (0,) * 3200
    assert right[round(1.5 * RATE) : round(1.6 * RATE)] == (2000,) * 1600
    assert right[2 * RATE : round(2.1 * RATE)] == (3000,) * 1600
    assert right[round(2.1 * RATE) : round(5.2 * RATE)] == (0,) * 49600
    waits = playback["waits"]
    assert waits[0] == {
        "turn_index": 1,
        "kind": "first_reply",
        "start_seconds": 0.4,
        "end_seconds": 1.2,
        "duration_seconds": pytest.approx(0.8),
    }
    assert any(
        w["kind"] == "transition"
        and w["start_seconds"] == 1.6
        and w["end_seconds"] == 2
        for w in waits
    )
    assert playback["segments"][0]["audio_seqs"] == [111, 112]
    assert playback["segments"][0]["intervals"][1]["start_seconds"] == 1.5
    assert playback["turns"][1]["first_playback_seconds"] == 5.2


def test_interrupted_frame_uses_saved_pcm_length_not_rounded_stop_time(tmp_path):
    item = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event("listen_stop_sent", 0.1),
            event("tts_sentence_start", 0.5, text="被打断的一句"),
            *frame(1, 0.5),
            event("playback_frame_started", 1, audio_seq=1),
            *frame(2, 0.6),
            event("playback_frame_started", 2, audio_seq=2),
            event("abort_requested", 2.02),
            event("playback_stopped", 2.04),
            event("tts_stop", 2.1),
        ],
        [100] * 1600,
        [1000] * 1600 + [2000] * 321,
        status="interrupted",
    )
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    _, right = samples(tmp_path, playback)
    assert right[2 * RATE : 2 * RATE + 321] == (2000,) * 321
    assert not any(right[2 * RATE + 321 :])
    assert playback["segments"][0]["end_seconds"] == 2 + 321 / RATE
    assert playback["segments"][0]["status"] == "interrupted"
    assert any(
        m["kind"] == "abort" and m["at_seconds"] == 2.02 for m in playback["markers"]
    )
    assert any(
        limit["code"] == "approximate_input_timing" for limit in playback["limitations"]
    )


def test_vad_replays_leading_and_trailing_noise_on_actual_send_clock(tmp_path):
    item = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event(
                "input_audio_frame_sent",
                0,
                pcm_offset_samples=0,
                samples=1600,
                sample_rate=RATE,
                stream="uplink",
                is_speech=False,
                listen_turn_id=1,
            ),
            event("speech_input_started", 0.3),
            event(
                "input_audio_frame_sent",
                0.3,
                pcm_offset_samples=1600,
                samples=1600,
                sample_rate=RATE,
                stream="uplink",
                is_speech=True,
                listen_turn_id=1,
            ),
            event("speech_input_finished", 0.4),
            event(
                "input_audio_frame_sent",
                0.5,
                pcm_offset_samples=3200,
                samples=1600,
                sample_rate=RATE,
                stream="uplink",
                is_speech=False,
                listen_turn_id=1,
            ),
        ],
        [500] * 1600,
        [],
        input_settings={"mode": "vad"},
    )
    wav(tmp_path / "turn-001.uplink.wav", [1] * 1600 + [500] * 1600 + [2] * 1600)
    item["audio"]["uplink"] = "turn-001.uplink.wav"
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    left, right = samples(tmp_path, playback)
    assert left[:1600] == (1,) * 1600
    assert left[4800:6400] == (500,) * 1600
    assert left[8000:9600] == (2,) * 1600
    assert not any(right)
    assert playback["turns"][0]["input_start_seconds"] == 0.3
    assert playback["turns"][0]["input_end_seconds"] == 0.4
    assert [s["kind"] for s in playback["turns"][0]["input_segments"]] == [
        "background",
        "speech",
        "background",
    ]
    assert playback["status"] == "ready"


@pytest.mark.parametrize("damage", ["missing_wav", "missing_seq", "truncated_pcm"])
def test_inconsistent_reply_artifacts_are_explicitly_incomplete(tmp_path, damage):
    item = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event("listen_stop_sent", 0.1),
            event("tts_sentence_start", 0.5, text="不伪造回听"),
            *frame(1, 0.5),
            event("playback_frame_started", 1, audio_seq=1),
            *frame(2, 0.6),
            event("playback_frame_started", 2, audio_seq=2),
        ],
        [100] * 1600,
        [1000] * 3200,
    )
    path = tmp_path / "turn-001.played.wav"
    if damage == "missing_wav":
        path.unlink()
    elif damage == "missing_seq":
        item["events"] = [
            e
            for e in item["events"]
            if not (e["event"] == "audio_received" and e["data"]["audio_seq"] == 1)
        ]
    else:
        wav(path, [1000] * 1000)
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    assert playback["status"] == "incomplete"
    assert any(
        limit["code"] == "reply_audio_unavailable" for limit in playback["limitations"]
    )
    assert not any(samples(tmp_path, playback)[1])


def test_no_valid_source_or_excessive_clock_does_not_publish_playable_silence(tmp_path):
    report = {
        "turns": [
            {"id": "missing", "events": [event("first_audio_sent", 0)], "audio": {}}
        ]
    }
    prepare_session_playback(report, tmp_path)
    assert report["session_playback"]["status"] == "unavailable"
    assert "path" not in report["session_playback"]
    item = turn(
        tmp_path,
        1,
        [event("first_audio_sent", 0)],
        [100] * 1600,
        [],
        ended_at_ns=BASE + 8000 * 1_000_000_000,
    )
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    assert report["session_playback"]["status"] == "unavailable"
    assert any(
        limit["code"] == "session_too_long"
        for limit in report["session_playback"]["limitations"]
    )


def test_native_24khz_reply_keeps_elapsed_duration_and_partial_frame(tmp_path):
    item = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event("listen_stop_sent", 0.1),
            event("tts_sentence_start", 0.5, text="二十四千赫兹"),
            event("audio_packet_received", 0.5, audio_seq=9),
            event(
                "audio_received",
                0.5,
                audio_seq=9,
                bytes=4800,
                sample_rate=24000,
                duration_ms=100,
                output_kind="answer",
            ),
            event("playback_frame_started", 1.3, audio_seq=9),
            event("audio_packet_received", 0.6, audio_seq=10),
            event(
                "audio_received",
                0.6,
                audio_seq=10,
                bytes=4800,
                sample_rate=24000,
                duration_ms=100,
                output_kind="answer",
            ),
            event("playback_frame_started", 2, audio_seq=10),
            event("playback_stopped", 2.03),
        ],
        [100] * 1600,
        [],
    )
    with wave.open(item["audio"]["played"], "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", ""))
        values = [1200] * 2400 + [2400] * 321
        output.writeframes(struct.pack(f"<{len(values)}h", *values))
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    assert playback["status"] == "ready"
    _, right = samples(tmp_path, playback)
    assert right[20800:22400] == (1200,) * 1600
    assert right[32000:32214] == (2400,) * 214
    assert not any(right[32214:])
    assert playback["segments"][0]["end_seconds"] == pytest.approx(2 + 321 / 24000)


def test_input_journal_and_source_turn_identity_recover_vad_tail(tmp_path):
    import json

    def sent(at, offset, owner, stream="uplink"):
        return event(
            "input_audio_frame_sent",
            at,
            pcm_offset_samples=offset,
            samples=1600,
            sample_rate=RATE,
            stream=stream,
            is_speech=False,
            listen_turn_id=owner,
        )

    first = turn(
        tmp_path,
        1,
        [event("first_audio_sent", 0), sent(0, 0, 1)],
        [100] * 1600,
        [],
        input_settings={"mode": "vad"},
    )
    first["audio"]["uplink"] = str(tmp_path / "turn-001.uplink.wav")
    wav(tmp_path / "turn-001.uplink.wav", [100] * 1600 + [200] * 1600 + [300] * 1600)
    # A previous microphone stream's event can be consumed by the next turn.
    second = turn(
        tmp_path,
        2,
        [sent(0.3, 1600, 1), event("first_audio_sent", 1), sent(1, 0, 2, "input")],
        [500] * 1600,
        [],
    )
    tail = sent(0.5, 3200, 1)
    journal = [sent(0, 0, 1), tail]
    (tmp_path / "client-events.jsonl").write_text(
        "\n".join(
            json.dumps(
                {"event": e["event"], "monotonic_ns": e["at_ns"], "data": e["data"]}
            )
            for e in journal
        )
        + "\n"
    )
    report = {"turns": [first, second]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    assert playback["status"] == "ready"
    left, _ = samples(tmp_path, playback)
    assert left[0:1600] == (100,) * 1600
    assert left[4800:6400] == (200,) * 1600
    assert left[8000:9600] == (300,) * 1600
    assert left[16000:17600] == (500,) * 1600
    assert len(playback["turns"][0]["input_segments"]) == 3
    assert len(playback["turns"][1]["input_segments"]) == 1


def test_missing_input_tail_timestamps_are_not_silently_approximated(tmp_path):
    item = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event(
                "input_audio_frame_sent",
                0,
                pcm_offset_samples=0,
                samples=1600,
                sample_rate=RATE,
                stream="input",
                is_speech=True,
                listen_turn_id=1,
            ),
        ],
        [100] * 1600 + [200] * 1600,
        [],
    )
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    assert playback["status"] == "incomplete"
    assert any(
        limit["code"] == "input_samples_without_timing"
        for limit in playback["limitations"]
    )
    left, _ = samples(tmp_path, playback)
    assert 200 not in left


def test_overlapping_send_packets_are_queued_without_erasing_pcm_or_moving_reply(
    tmp_path,
):
    item = turn(
        tmp_path,
        1,
        [
            event("first_audio_sent", 0),
            event(
                "input_audio_frame_sent",
                0,
                pcm_offset_samples=0,
                samples=1600,
                sample_rate=RATE,
                stream="input",
                is_speech=True,
                listen_turn_id=1,
            ),
            event(
                "input_audio_frame_sent",
                0.05,
                pcm_offset_samples=1600,
                samples=1600,
                sample_rate=RATE,
                stream="input",
                is_speech=True,
                listen_turn_id=1,
            ),
            event("listen_stop_sent", 0.15),
            event("tts_sentence_start", 0.3, text="收到"),
            *frame(1, 0.3),
            event("playback_frame_started", 0.5, audio_seq=1),
        ],
        [100] * 1600 + [200] * 1600,
        [300] * 1600,
    )
    original_times = [e["at_ns"] for e in item["events"]]
    report = {"turns": [item]}
    prepare_session_playback(report, tmp_path)
    playback = report["session_playback"]
    left, right = samples(tmp_path, playback)
    assert left[:3200] == (100,) * 1600 + (200,) * 1600
    assert right[8000:9600] == (300,) * 1600
    assert [e["at_ns"] for e in item["events"]] == original_times
    assert playback["status"] == "ready"
    assert playback["turns"][0]["input_end_seconds"] == 0.15
    assert playback["turns"][0]["input_segments"][-1]["end_seconds"] == 0.2
    assert playback["turns"][0]["input_segments"][-1]["sent_end_seconds"] == 0.15
    assert any(
        limit["code"] == "input_packet_overlap" and limit["max_shift_seconds"] == 0.05
        for limit in playback["limitations"]
    )
