"""Derive spoken-segment timing from ordered wire boundaries and played frames."""


def ordered_playback_frames(turn):
    """Recover v2 PCM append order from observation evidence, never estimated at_ns."""
    frames = [
        e for e in turn.get("events", []) if e["event"] == "playback_frame_started"
    ]
    if frames and all(
        e.get("data", {}).get("timing_model") == "browser_audio_context_v2"
        and isinstance(e["data"].get("playback_observed_at_ns"), int)
        and isinstance(e["data"].get("audio_seq"), int)
        for e in frames
    ):
        return sorted(
            frames,
            key=lambda e: (
                e["data"]["playback_observed_at_ns"],
                e["data"]["audio_seq"],
            ),
        )
    return frames


def playback_clock(turn):
    """Validate browser playback against packet receipt in the same client clock.

    A legacy AudioContext mapping can run behind performance.now() after a
    suspension. Neither its derived wall time nor packet arrival can recover the
    actual playback instant. Keep raw events unchanged and never replace an
    unknown first frame with a later valid-looking one. New v2 observation
    evidence can retain a confirmed prefix when only its tail is uncertain.
    """
    events = turn.get("events", [])
    frames = ordered_playback_frames(turn)
    received = {}
    for event in events:
        if event["event"] in {"audio_received", "audio_packet_received"}:
            seq = event.get("data", {}).get("audio_seq")
            if seq is not None:
                # Decoded audio receipt is the stronger causal boundary.
                received[seq] = max(received.get(seq, event["at_ns"]), event["at_ns"])
    issues, estimated, prefix = [], 0, 0
    prefix_open = bool(frames) and all(
        e.get("data", {}).get("timing_model") == "browser_audio_context_v2"
        and isinstance(e["data"].get("playback_observed_at_ns"), int)
        and isinstance(e["data"].get("audio_seq"), int)
        for e in frames
    )
    for frame in frames:
        data = frame.get("data", {})
        browser = turn.get("playback_source") == "browser_audio_context" or (
            data.get("source") == "browser_audio_context"
            or data.get("timing_model", "").startswith("browser_audio_context")
        )
        if not browser:
            continue
        confirmed = (
            data.get("timing_model") == "browser_audio_context_v2"
            and data.get("timing_method") == "output_timestamp"
            and data.get("output_start_confirmed") is True
            and data.get("output_end_confirmed") is True
        )
        if data.get("timing_method") == "context_clock_estimate" or (
            data.get("timing_model") == "browser_audio_context_v2" and not confirmed
        ):
            estimated += 1
        seq = data.get("audio_seq")
        arrived = received.get(seq)
        if arrived is None or frame["at_ns"] < arrived:
            prefix_open = False
            issues.append(
                {
                    "code": (
                        "missing_playback_receipt"
                        if arrived is None
                        else "playback_before_receipt"
                    ),
                    "audio_seq": seq,
                    "playback_at_ns": frame["at_ns"],
                    "received_at_ns": arrived,
                }
            )
        if prefix_open and confirmed:
            prefix += 1
        else:
            prefix_open = False
    partial = bool(prefix and estimated)
    trusted = prefix if partial else 0 if issues or estimated else len(frames)
    return {
        "status": (
            "partial"
            if partial
            else (
                "invalid"
                if issues
                else "estimated" if estimated else "valid" if frames else "unavailable"
            )
        ),
        "invalid_frame_count": len(issues),
        "estimated_frame_count": estimated,
        "trusted_frame_count": trusted,
        "unlocated_frame_count": len(frames) - trusted,
        "issues": issues[:5],
    }


def validated_playback_frames(turn):
    return ordered_playback_frames(turn)[: playback_clock(turn)["trusted_frame_count"]]


def input_end_event(turn):
    if turn.get("input_type") == "text":
        return "text_sent"
    if turn.get("sensor"):
        return "sensor_sent"
    return (
        "speech_input_finished"
        if turn.get("input_settings", {}).get("mode") == "vad"
        else "listen_stop_sent"
    )


def analyze_reply_timing(turn):
    events = turn.get("events", [])
    clock = playback_clock(turn)
    zero_event = input_end_event(turn)
    zero = next((e["at_ns"] for e in events if e["event"] == zero_event), None)
    stopped = next(
        (e["at_ns"] for e in events if e["event"] == "playback_stopped"), None
    )
    sentences, received = [], {}
    played = {
        e.get("data", {}).get("audio_seq"): e["at_ns"]
        for e in validated_playback_frames(turn)
    }
    active = None
    for event in events:
        name, data = event["event"], event.get("data", {})
        seq = data.get("audio_seq")
        if name == "tts_sentence_start":
            if active is not None:
                active["closed"] = True
            active = dict(
                text=data.get("text", ""),
                seqs=[],
                closed=False,
                is_session_output=data.get("is_session_output", False),
            )
            sentences.append(active)
        elif name in {"tts_sentence_end", "tts_stop"}:
            if active is not None:
                active["closed"] = True
            active = None
        elif name == "audio_packet_received" and active is not None and seq is not None:
            # WebSocket message order defines the boundary, not when buffered audio plays.
            active["seqs"].append(seq)
        if name == "audio_received" and seq is not None:
            received[seq] = data

    def relative(value):
        return (value - zero) / 1e9 if value is not None and zero is not None else None

    rows = []
    previous_start = previous_end = None
    for index, sentence in enumerate(sentences, 1):
        intervals, kinds = [], set()
        clipped = False
        for seq in sentence["seqs"]:
            frame = received.get(seq, {})
            if frame.get("output_kind"):
                kinds.add(frame["output_kind"])
            start = played.get(seq)
            duration = frame.get("duration_ms")
            if (
                start is None
                or duration is None
                or frame.get("discarded_after_abort")
                or (stopped is not None and start >= stopped)
                or frame.get("server_listen_turn_id")
                not in {
                    None,
                    turn.get("server_listen_turn_id", turn.get("listen_turn_id")),
                }
            ):
                continue
            end = start + round(duration * 1e6)
            if stopped is not None and end > stopped:
                end, clipped = stopped, True
            intervals.append((start, end))
        intervals.sort()
        start = intervals[0][0] if intervals else None
        end = max(pair[1] for pair in intervals) if intervals else None
        complete = (
            bool(intervals)
            and sentence["closed"]
            and len(intervals) == len(sentence["seqs"])
            and not clipped
        )
        status = (
            "timing_unavailable"
            if clock["status"] in {"invalid", "estimated"}
            or (clock["status"] == "partial" and not intervals)
            else (
                "completed"
                if complete
                else (
                    "not_played"
                    if not intervals
                    else "interrupted" if stopped is not None else "incomplete"
                )
            )
        )
        rows.append(
            dict(
                index=index,
                text=sentence["text"],
                audio_seqs=sentence["seqs"],
                kind=(
                    "greeting"
                    if sentence["is_session_output"]
                    else (
                        next(iter(kinds))
                        if len(kinds) == 1
                        else "mixed" if kinds else "unknown"
                    )
                ),
                start_seconds=relative(start),
                end_seconds=relative(end),
                since_previous_start_seconds=(
                    (start - previous_start) / 1e9
                    if start is not None and previous_start is not None
                    else None
                ),
                gap_seconds=(
                    max(0, start - previous_end) / 1e9
                    if start is not None and previous_end is not None
                    else None
                ),
                pcm_seconds=sum(b - a for a, b in intervals) / 1e9,
                played_frames=len(intervals),
                status=status,
            )
        )
        if not sentence["is_session_output"]:
            previous_start, previous_end = start, end
    limitations = [
        (
            "0 秒取客户端发送 sensor 指令的时刻；本轮没有上传语音，不经过 VAD / ASR。"
            if zero_event == "sensor_sent"
            else (
                "0 秒取客户端语音 WAV 播送完毕的时刻，之后仍发送底噪，没有发送 listen/stop；WAV 内部静音也计入素材时长，不等于人工标注的最后发声点。"
                if zero_event == "speech_input_finished"
                else "0 秒取客户端发送 listen/stop 的时刻，代表本次语音输入结束。"
            )
        ),
        "按服务端播报分段，通过音频包序号关联模拟播放；不把收到 sentence_start 当成播放开始。",
        "句间空档按末帧开始加 PCM 时长估算，包含播放器调度误差，不包含音频内部自带的静音。",
    ]
    if zero is None:
        limitations.append("缺少输入结束时间，不能计算说完后的等待。")
    if not sentences:
        limitations.append("缺少播报分段消息，不能从 TTS 请求耗时推断每句播放时间。")
    if clock["status"] == "invalid":
        limitations.append(
            "历史浏览器播放时钟无效：播放帧早于对应收包，或缺少对应收包记录；无法还原真实首音与句间时间，保留原始音频供回听。"
        )
    elif clock["status"] == "estimated":
        limitations.append(
            "浏览器未提供可靠输出时钟，仅有 AudioContext 估计值；不将估计值作为真实首音或句间时间，保留原始音频供回听。"
        )
    elif clock["status"] == "partial":
        limitations.append(
            "首音与连续已确认播放帧使用浏览器输出时钟；后续未确认尾帧不定位，原始完整回复保留供回听。"
        )
    return dict(
        zero_at_ns=zero,
        zero_event=zero_event,
        source=turn.get("playback_source", "client_simulated_playback"),
        playback_clock=clock,
        sentences=rows,
        max_gap_seconds=max(
            (r["gap_seconds"] for r in rows if r["gap_seconds"] is not None),
            default=None,
        ),
        limitations=limitations,
    )
