"""Derive spoken-segment timing from ordered wire boundaries and played frames."""


def input_end_event(turn):
    return (
        "speech_input_finished"
        if turn.get("input_settings", {}).get("mode") == "vad"
        else "listen_stop_sent"
    )


def analyze_reply_timing(turn):
    events = turn.get("events", [])
    zero_event = input_end_event(turn)
    zero = next((e["at_ns"] for e in events if e["event"] == zero_event), None)
    stopped = next(
        (e["at_ns"] for e in events if e["event"] == "playback_stopped"), None
    )
    sentences, received, played = [], {}, {}
    active = None
    for event in events:
        name, data = event["event"], event.get("data", {})
        seq = data.get("audio_seq")
        if name == "tts_sentence_start":
            if active is not None:
                active["closed"] = True
            active = dict(text=data.get("text", ""), seqs=[], closed=False)
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
        elif name == "playback_frame_started" and seq is not None:
            played.setdefault(seq, event["at_ns"])

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
                not in {None, turn.get("listen_turn_id")}
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
            "completed"
            if complete
            else (
                "not_played"
                if not intervals
                else "interrupted" if stopped is not None else "incomplete"
            )
        )
        rows.append(
            dict(
                index=index,
                text=sentence["text"],
                audio_seqs=sentence["seqs"],
                kind=(
                    next(iter(kinds))
                    if len(kinds) == 1
                    else "mixed" if kinds else "unknown"
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
        previous_start, previous_end = start, end
    limitations = [
        (
            "0 秒取客户端语音 WAV 播送完毕的时刻，之后仍发送底噪，没有发送 listen/stop；WAV 内部静音也计入素材时长，不等于人工标注的最后发声点。"
            if zero_event == "speech_input_finished"
            else "0 秒取客户端发送 listen/stop 的时刻，代表本次语音输入结束。"
        ),
        "按服务端播报分段，通过音频包序号关联模拟播放；不把收到 sentence_start 当成播放开始。",
        "句间空档按末帧开始加 PCM 时长估算，包含播放器调度误差，不包含音频内部自带的静音。",
    ]
    if zero is None:
        limitations.append("缺少输入结束时间，不能计算说完后的等待。")
    if not sentences:
        limitations.append("缺少播报分段消息，不能从 TTS 请求耗时推断每句播放时间。")
    return dict(
        zero_at_ns=zero,
        zero_event=zero_event,
        source="client_simulated_playback",
        sentences=rows,
        max_gap_seconds=max(
            (r["gap_seconds"] for r in rows if r["gap_seconds"] is not None),
            default=None,
        ),
        limitations=limitations,
    )
