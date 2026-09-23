"""One explicit pair per row; request identity, never proximity, links outputs."""

import copy
from collections import defaultdict

from .memory_evidence import candidate_decisions
from .reply_timing import ordered_playback_frames, validated_playback_frames


def _identity(item):
    return item.get("clock_id"), item.get("span_id")


def _first(items):
    return min(items, key=lambda p: p["plot_start_ns"], default=None)


def _interval(left, right, absolute):
    same = left["source"] == right["source"] and left.get("clock_id") == right.get(
        "clock_id"
    )
    same = same and (left["source"] == "client" or left.get("clock_id") is not None)
    calibration = left.get("clock_calibration") or right.get("clock_calibration")
    basis = (
        "monotonic"
        if same
        else (
            ("wall_calibrated" if calibration else "wall_unaligned")
            if absolute
            else "unavailable"
        )
    )
    delta = (
        (right["start_ns"] - left["start_ns"])
        if same
        else (right["plot_start_ns"] - left["plot_start_ns"])
    ) / 1e9
    return dict(
        from_label=left["label"],
        to_label=right["label"],
        start_ns=left["plot_start_ns"],
        end_ns=right["plot_start_ns"],
        duration_seconds=None if basis == "unavailable" else delta,
        clock_basis=basis,
        uncertainty_seconds=(
            calibration["uncertainty_ns"] / 1e9 if calibration and not same else None
        ),
        note=(
            "同一时钟的实际时间差"
            if same
            else (
                (
                    f"跨端校准估计，采样不确定范围 ±{calibration['uncertainty_ns'] / 1e9:.6f} 秒；不等同于纯网络耗时"
                    if calibration
                    else "跨端墙钟差，未校时；包含时钟偏差，不等同于网络耗时"
                )
                if absolute
                else "缺少共同墙钟，不计算跨端间隔"
            )
        ),
    )


def _turn_rows(turn, index, own, playback):
    zero = playback["zero_at_ns"]
    trace = playback["vas_timeline"]
    server = [dict(m, source="server") for lane in own for m in lane.get("markers", [])]
    events = turn.get("events", [])
    rows = []

    def server_point(name, label, identity=None):
        p = _first(
            [
                m
                for m in server
                if m["event"] == name and (identity is None or _identity(m) == identity)
            ]
        )
        return dict(p, label=label) if p else None

    def client_point(names, label):
        candidates = [
            e for e in events if e["event"] in names and isinstance(e.get("at_ns"), int)
        ]
        e = min(candidates, key=lambda e: e["at_ns"], default=None)
        return client_at(e["event"], e["at_ns"], label) if e else None

    def client_at(name, at, label):
        return dict(
            event=name,
            label=label,
            source="client",
            clock_id="client",
            start_ns=at,
            plot_start_ns=at - zero,
            color="#226849",
        )

    def pair(key, label, left, right, missing=(), llm_number=None):
        points = [copy.deepcopy(p) for p in (left, right) if p is not None]
        for p in points:
            p.update(turn_index=index, critical=True)
        rows.append(
            dict(
                label=label,
                source="mixed",
                category="key_moments",
                turn_index=index,
                key_id=key,
                llm_number=llm_number,
                markers=points,
                segments=[],
                intervals=(
                    [_interval(left, right, trace["mode"] == "wall")]
                    if left and right
                    else []
                ),
                missing=list(missing) if not left or not right else [],
            )
        )

    mode = next(
        (
            e.get("data", {}).get("mode")
            for e in events
            if e["event"] == "listen_start_sent"
        ),
        None,
    )
    mode = mode or turn.get("input_settings", {}).get("mode")
    final = server_point("asr_final", "ASR 完成")
    stops = [e for e in events if e["event"] == "listen_stop_sent"]
    stop = (
        client_at("listen_stop_sent", stops[-1]["at_ns"], "STOP 发出")
        if stops
        else None
    )
    speech_end = (
        stop
        if mode == "manual"
        else client_point({"speech_input_finished"}, "说话结束")
    )
    first_play = next(
        (
            m
            for m in playback.get("markers", [])
            if m.get("turn_index") == index
            and m["kind"] == "first_playback"
            and isinstance(m.get("at_seconds"), (int, float))
        ),
        None,
    )
    play_label = "开始播放" + (
        "（模拟）" if turn.get("playback_source") == "simulated_player" else ""
    )
    played = (
        client_at(
            "first_playback", zero + round(first_play["at_seconds"] * 1e9), play_label
        )
        if first_play
        else None
    )
    pair("first_reply", "说完 → 首声", speech_end, played, ["输入结束或首帧播放未采集"])
    if mode == "manual":
        sent = client_point({"first_audio_sent", "input_audio_frame_sent"}, "首帧发送")
        pair("ptt", "PTT · 音频发送", sent, stop, ["首帧发送或 STOP 未采集"])
        pair("asr_final", "PTT · 识别收尾", stop, final, ["STOP 或 ASR 完成未采集"])
    elif mode in ("auto", "realtime", "vad"):
        pair(
            "vad_local",
            "VAS 本地 VAD → ASR",
            server_point("local_vad_endpoint_detected", "本地 VAD 结束"),
            final,
            ["本地 VAD 结束或 ASR 完成未采集"],
        )
        pair(
            "vad_asr",
            "ASR VAD → ASR",
            server_point("asr_endpoint_detected", "ASR VAD 结束"),
            final,
            ["ASR VAD 结束或 ASR 完成未采集"],
        )
    elif final:
        pair("asr_final", "ASR 完成", None, final)

    decisions = candidate_decisions(turn.get("vas_events", []))
    llm_events = defaultdict(list)
    for m in server:
        if m["event"] in ("llm_request_started", "llm_first_token") and m.get(
            "span_id"
        ):
            llm_events[_identity(m)].append(m)
    llms = {}
    for n, identity in enumerate(
        sorted(llm_events, key=lambda key: _first(llm_events[key])["plot_start_ns"]), 1
    ):
        start = server_point("llm_request_started", "请求提交", identity)
        attempt = (start or {}).get("data", {}).get("pipeline_attempt_id")
        if decisions.get(attempt, {}).get("selection") == "discarded":
            continue
        token = server_point("llm_first_token", "首包类型未采集", identity)
        if token:
            kind = token.get("data", {}).get("delta_kind")
            token["label"] = {"text": "文字首包", "tool": "工具首包"}.get(
                kind, "首包类型未采集"
            )
            token["evidence_note"] = (
                "工具参数不等于文字；这里只显示本次 LLM 请求实际记录的首个增量。"
            )
        if not llms and final:
            pair(
                "asr_to_llm", f"ASR → LLM #{n}", final, start, ["LLM 请求提交未采集"], n
            )
        llms[identity] = (n, start, token)

    # Walk explicit parent spans in the same clock. Common chat parents do not
    # identify which sibling LLM generated a TTS segment.
    nodes = {_identity(s): s for lane in own for s in lane.get("segments", [])}

    def owner(segment):
        clock = segment.get("clock_id")
        parent = segment.get("parent_span_id")
        seen = set()
        while parent and parent not in seen:
            key = (clock, parent)
            if key in llms:
                return key
            seen.add(parent)
            parent = nodes.get(key, {}).get("parent_span_id")
        return None

    groups = defaultdict(list)
    for lane in own:
        if lane.get("category") != "tts_request":
            continue
        for s in lane.get("segments", []):
            scope = owner(s)
            groups[
                (
                    scope,
                    s.get("clock_id"),
                    s.get("output_id") or s.get("span_id"),
                    s.get("output_kind"),
                )
            ].append(s)
    frames = [
        e
        for e in validated_playback_frames(turn)
        if not e.get("data", {}).get("is_session_output")
    ]

    def output_rows(scope, segments, output_kind):
        candidates = []
        for segment in segments:
            pcm = server_point("tts_first_pcm", "TTS 首音", _identity(segment))
            if pcm:
                candidates.append((pcm, segment))
        n, start, token = llms.get(scope, (None, None, None))
        kind = {"pre_speech": "过渡语", "filler": "临时回复", "answer": "正式回复"}.get(
            output_kind, "回复"
        )
        prefix = f"LLM #{n} · {kind}" if n else f"{kind} · LLM 归属未采集"
        if not candidates:
            submitted = _first(
                [
                    p
                    for s in segments
                    if (
                        p := server_point(
                            "tts_request_started", "TTS 提交", _identity(s)
                        )
                    )
                ]
            )
            pair("tts_first", prefix, submitted, None, ["该输出首音未采集"], n)
            return
        pcm, segment = min(candidates, key=lambda pair: pair[0]["plot_start_ns"])
        origin = (
            (token or start)
            if scope
            else server_point("tts_request_started", "TTS 提交", _identity(segment))
        )
        pair("tts_first", prefix, origin, pcm, ["对应请求起点未采集"], n)
        # Sentence evidence has already checked session + turn + packet/order.
        evidence = segment.get("tts_evidence", {})
        seqs = (
            set(evidence.get("audio_seqs", []))
            if evidence.get("status") == "matched"
            else set()
        )
        first = next(
            (
                e
                for e in ordered_playback_frames(turn)
                if e.get("data", {}).get("audio_seq") in seqs
            ),
            None,
        )
        frame = first if first is not None and first in frames else None
        played = (
            client_at("playback_frame_started", frame["at_ns"], play_label)
            if frame
            else None
        )
        pair(
            "output_playback",
            prefix + " · 播放",
            pcm,
            played,
            ["该首音的播放时间未关联"],
            n,
        )

    for scope, (n, start, token) in llms.items():
        pair("llm_first", f"LLM #{n} · 首输出", start, token, ["该请求首包未采集"], n)
        for (parent, _, _, kind), segments in groups.items():
            if parent == scope:
                output_rows(scope, segments, kind)
    for (parent, _, _, kind), segments in groups.items():
        if parent is None:
            output_rows(None, segments, kind)
    return rows


def add_key_moment_lanes(report):
    playback = report.get("session_playback", {})
    trace = playback.get("vas_timeline")
    if not trace or playback.get("zero_at_ns") is None:
        return
    lanes = [lane for lane in trace["lanes"] if lane.get("category") != "key_moments"]
    result = [lane for lane in lanes if lane.get("turn_index") is None]
    for index, turn in enumerate(report.get("turns", []), 1):
        own = [lane for lane in lanes if lane.get("turn_index") == index]
        rows = _turn_rows(turn, index, own, playback)
        result.extend(rows)
        result.extend(sorted(own, key=lambda lane: lane.get("category") != "vad"))
        for row in rows:
            for p in row["markers"]:
                trace["axis_start_seconds"] = min(
                    trace.get("axis_start_seconds", 0), p["plot_start_ns"] / 1e9
                )
                trace["axis_end_seconds"] = max(
                    trace.get("axis_end_seconds", 0), p["plot_start_ns"] / 1e9
                )
    trace["lanes"] = result
