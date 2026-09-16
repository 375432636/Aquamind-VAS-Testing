"""Reconstruct legacy *simulated* playback from recorded PCM availability.

Call only on a copy. Never retime real browser playback or erase source evidence.
"""


def reconstruct_simulated_playback(result, journal):
    ready = {
        e["data"]["audio_seq"]: e["monotonic_ns"]
        for e in journal
        if e.get("event") == "pcm"
        and isinstance(e.get("data", {}).get("audio_seq"), int)
    }
    changed = 0
    for turn in [result.get("startup", {}), *result.get("turns", [])]:
        events = turn.get("events", [])
        frames = [e for e in events if e["event"] == "playback_frame_started"]
        packets = {
            e["data"].get("audio_seq"): e["data"]
            for e in events
            if e["event"] == "audio_received"
        }
        if not frames or any(
            e["data"].get("source") != "simulated_player"
            or e["data"].get("timing_model")
            or e["data"].get("audio_seq") not in ready
            or e["data"].get("audio_seq") not in packets
            or not packets[e["data"]["audio_seq"]].get("sample_rate")
            for e in frames
        ):
            continue
        # A partial legacy frame cannot be unambiguously expanded after cancellation.
        if any(e["event"] == "playback_stopped" for e in events):
            continue
        cursor = frames[0]["at_ns"]
        for frame in frames:
            seq = frame["data"]["audio_seq"]
            packet = packets[seq]
            start = max(cursor, ready[seq])
            samples, rate = packet["bytes"] // 2, packet["sample_rate"]
            cursor = start + round(samples / rate * 1e9)
            frame["data"].update(
                original_playback_at_ns=frame["at_ns"],
                pcm_ready_at_ns=ready[seq],
                sample_count=samples,
                sample_rate=rate,
                scheduled_end_ns=cursor,
                timing_model="reconstructed_sample_clock_v1",
            )
            frame["at_ns"] = start
            changed += 1
        events.sort(key=lambda e: e["at_ns"])
    if changed:
        result["playback_reconstruction"] = {
            "frames": changed,
            "source": "recorded_pcm_availability",
            "note": "模拟播放重建：按已记录音频就绪时间与样本游标恢复连续播放，原始录制另存。",
        }
    return changed
