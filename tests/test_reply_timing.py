from voice_scenarios.reply_timing import analyze_reply_timing


def event(name, seconds, **data):
    return {"event": name, "at_ns": round(seconds * 1e9), "data": data}


def test_vad_zero_is_speech_fixture_end_not_microphone_stream_end():
    result = analyze_reply_timing(
        {
            "input_settings": {"mode": "vad"},
            "events": [
                event("speech_input_finished", 2),
                event("background_noise_started", 2),
                event("tts_sentence_start", 3, text="回复"),
                event("audio_packet_received", 3, audio_seq=1),
                event("audio_received", 3, audio_seq=1, duration_ms=60),
                event("playback_frame_started", 3.1, audio_seq=1),
                event("tts_stop", 3.2),
            ],
        }
    )
    assert result["zero_at_ns"] == 2_000_000_000
    assert result["zero_event"] == "speech_input_finished"
    assert result["sentences"][0]["start_seconds"] == 1.1


def test_sentence_playback_uses_packet_sequence_not_control_receipt():
    turn = {
        "listen_turn_id": 1,
        "events": [
            event("listen_stop_sent", 0),
            event("tts_sentence_start", 0.10, text="第一句"),
            event("audio_packet_received", 0.10, audio_seq=1),
            event(
                "audio_received",
                0.10,
                audio_seq=1,
                duration_ms=200,
                output_kind="answer",
            ),
            event("playback_frame_started", 0.11, audio_seq=1),
            event("audio_packet_received", 0.15, audio_seq=2),
            event(
                "audio_received",
                0.15,
                audio_seq=2,
                duration_ms=200,
                output_kind="answer",
            ),
            event("tts_sentence_start", 0.20, text="第二句"),
            event("audio_packet_received", 0.21, audio_seq=3),
            event(
                "audio_received",
                0.21,
                audio_seq=3,
                duration_ms=200,
                output_kind="answer",
            ),
            # An old buffered frame plays after the second sentence's control message.
            event("playback_frame_started", 0.31, audio_seq=2),
            event("tts_stop", 0.40),
            event("playback_frame_started", 0.52, audio_seq=3),
        ],
    }
    result = analyze_reply_timing(turn)
    first, second = result["sentences"]
    assert first["start_seconds"] == 0.11
    assert first["end_seconds"] == 0.51
    assert second["start_seconds"] == 0.52
    assert second["since_previous_start_seconds"] == 0.41
    assert second["gap_seconds"] == 0.01
    assert first["pcm_seconds"] == 0.4
    assert second["status"] == "completed"


def test_interruption_clips_playback_and_does_not_count_discarded_audio():
    result = analyze_reply_timing(
        {
            "events": [
                event("listen_stop_sent", 0),
                event("tts_sentence_start", 1, text="被打断的句子"),
                event("audio_packet_received", 1, audio_seq=1),
                event("audio_received", 1, audio_seq=1, duration_ms=200),
                event("playback_frame_started", 1.01, audio_seq=1),
                event("playback_stopped", 1.10, reason="interrupt"),
                event("audio_packet_received", 1.12, audio_seq=2),
                event(
                    "audio_received",
                    1.12,
                    audio_seq=2,
                    duration_ms=200,
                    discarded_after_abort=True,
                ),
                event("tts_stop", 1.15),
                event("tts_sentence_start", 1.20, text="没有播放的句子"),
            ]
        }
    )
    first, second = result["sentences"]
    assert first["status"] == "interrupted"
    assert first["end_seconds"] == 1.10
    assert first["pcm_seconds"] == 0.09
    assert first["played_frames"] == 1
    assert second["start_seconds"] is None
    assert second["gap_seconds"] is None


def test_missing_sentence_boundaries_or_stop_does_not_invent_playback_times():
    result = analyze_reply_timing({"events": [event("playback_started", 10)]})
    assert result["sentences"] == []
    assert result["zero_at_ns"] is None
    assert result["limitations"]
