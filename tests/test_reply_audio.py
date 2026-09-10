"""Report regeneration exports playable sentence audio from existing artifacts."""

import json
import struct
import wave

import pytest

from voice_scenarios.__main__ import create_report


def event(name, seconds, **data):
    return {"event": name, "at_ns": round(seconds * 1e9), "data": data}


def wav(path, values):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", ""))
        output.writeframes(struct.pack(f"<{len(values)}h", *values))


def read_clip(directory, clip):
    assert clip["status"] == "ready", clip
    with wave.open(str(directory / clip["path"])) as audio:
        assert audio.getframerate() == 16000
        data = audio.readframes(audio.getnframes())
    return struct.unpack(f"<{len(data)//2}h", data)


def save_run(directory, events, received, played):
    wav(directory / "turn-001.input.wav", [0] * 1600)
    wav(directory / "turn-001.received.wav", received)
    wav(directory / "turn-001.played.wav", played)
    result = {
        "name": "reply-audio",
        "status": "passed",
        "turns": [
            {
                "id": "one",
                "status": "completed",
                "listen_turn_id": 1,
                "playback_source": "simulated_player",
                "events": events,
                "audio": {
                    kind: str(directory / f"turn-001.{kind}.wav")
                    for kind in ("input", "received", "played")
                },
            }
        ],
    }
    (directory / "result.json").write_text(json.dumps(result))


def frame(seq, at, **extra):
    return event(
        "audio_received",
        at,
        audio_seq=seq,
        bytes=3200,
        duration_ms=100,
        sample_rate=16000,
        **extra,
    )


def test_report_exports_each_sentence_by_packets_not_elapsed_time(tmp_path):
    events = [
        event("listen_stop_sent", 0),
        frame(9, 0.5),
        event("playback_frame_started", 0.5, audio_seq=9),
        event("tts_sentence_start", 1, text="第一句<&>"),
        event("audio_packet_received", 1, audio_seq=10),
        frame(10, 1),
        event("playback_frame_started", 1.1, audio_seq=10),
        event("audio_packet_received", 1.2, audio_seq=11),
        frame(11, 1.2),
        event("tts_sentence_start", 1.3, text="第二句"),
        event("audio_packet_received", 2, audio_seq=12),
        frame(12, 2),
        # Buffered old audio starts after the next sentence control.
        event("playback_frame_started", 3, audio_seq=11),
        event("playback_frame_started", 4, audio_seq=12),
        event("tts_stop", 5),
    ]
    pcm = [999] * 1600 + [1000] * 1600 + [2000] * 1600 + [3000] * 1600
    save_run(tmp_path, events, pcm, pcm)
    raw = (tmp_path / "result.json").read_bytes()
    report = create_report(tmp_path)
    first, second = report["turns"][0]["reply_timing"]["sentences"]
    assert first["text"] == "第一句<&>" and second["text"] == "第二句"
    for source in ("received", "played"):
        assert read_clip(tmp_path, first["audio"][source]) == tuple(
            [1000] * 1600 + [2000] * 1600
        )
        assert read_clip(tmp_path, second["audio"][source]) == tuple([3000] * 1600)
    assert first["audio"]["played"]["duration_seconds"] == 0.2
    assert second["audio"]["played"]["duration_seconds"] == 0.1
    assert (tmp_path / "result.json").read_bytes() == raw
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["turns"][0]["reply_timing"] == report["turns"][0]["reply_timing"]
    assert first["audio"]["played"]["path"] in (tmp_path / "turn-001.html").read_text()


def test_interruption_replays_exact_saved_samples_and_keeps_unplayed_received_audio(
    tmp_path,
):
    events = [
        event("listen_stop_sent", 0),
        event("tts_sentence_start", 1, text="被打断的回复"),
        event("audio_packet_received", 1, audio_seq=1),
        frame(1, 1),
        event("playback_frame_started", 1, audio_seq=1),
        event("audio_packet_received", 1.01, audio_seq=2),
        frame(2, 1.01),
        event("playback_frame_started", 2, audio_seq=2),
        event("playback_stopped", 2.02, reason="interrupt"),
        event("tts_sentence_start", 2.03, text="收到但未播放"),
        event("audio_packet_received", 2.04, audio_seq=3),
        frame(3, 2.04, discarded_after_abort=True),
        event("tts_stop", 2.05),
    ]
    received = [1000] * 1600 + [2000] * 1600 + [3000] * 1600
    # Persisted PCM, rather than rounded event timestamps, is authoritative.
    played = [1000] * 1600 + [2000] * 321
    save_run(tmp_path, events, received, played)
    first, second = create_report(tmp_path)["turns"][0]["reply_timing"]["sentences"]
    assert first["status"] == "interrupted"
    assert read_clip(tmp_path, first["audio"]["played"]) == tuple(played)
    assert first["audio"]["played"]["complete"] is False
    assert first["audio"]["played"]["samples"] == 1921
    assert read_clip(tmp_path, first["audio"]["received"]) == tuple(received[:3200])
    assert second["status"] == "not_played"
    assert second["audio"]["played"]["reason"] == "not_played"
    assert read_clip(tmp_path, second["audio"]["received"]) == tuple([3000] * 1600)


@pytest.mark.parametrize(
    "damage, reason",
    [
        ("size", "wav_packet_mismatch"),
        ("sequence", "missing_packet"),
        ("missing_file", "missing_or_invalid_wav"),
    ],
)
def test_incomplete_artifacts_do_not_create_mislabelled_clips(tmp_path, damage, reason):
    events = [
        event("tts_sentence_start", 1, text="只有确定关联才可回听"),
        event("audio_packet_received", 1, audio_seq=1),
        frame(1, 1),
        event("playback_frame_started", 1, audio_seq=1),
        event("tts_stop", 2),
    ]
    if damage == "sequence":
        events[1]["data"]["audio_seq"] = 2
    save_run(tmp_path, events, [1000] * 1600, [1000] * 1600)
    if damage == "size":
        wav(tmp_path / "turn-001.received.wav", [1000] * 800)
    elif damage == "missing_file":
        (tmp_path / "turn-001.received.wav").unlink()
    sentence = create_report(tmp_path)["turns"][0]["reply_timing"]["sentences"][0]
    clip = sentence["audio"]["received"]
    assert clip["status"] == "unavailable" and clip["reason"] == reason
    assert "path" not in clip
    assert sentence["text"] == "只有确定关联才可回听"


def test_moved_report_uses_adjacent_wavs_and_regeneration_is_idempotent(tmp_path):
    events = [
        event("tts_sentence_start", 1, text="可迁移"),
        event("audio_packet_received", 1, audio_seq=10),
        frame(10, 1),
        event("playback_frame_started", 1, audio_seq=10),
        event("tts_stop", 2),
    ]
    save_run(tmp_path, events, [1000] * 1600, [1000] * 1600)
    path = tmp_path / "result.json"
    result = json.loads(path.read_text())
    for kind in ("received", "played"):
        result["turns"][0]["audio"][kind] = f"/no-longer-present/turn-001.{kind}.wav"
    path.write_text(json.dumps(result))
    first = create_report(tmp_path)["turns"][0]["reply_timing"]
    second = create_report(tmp_path)["turns"][0]["reply_timing"]
    assert first == second
    assert read_clip(tmp_path, second["sentences"][0]["audio"]["played"]) == tuple(
        [1000] * 1600
    )
