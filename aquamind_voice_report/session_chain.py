"""Whole-session stages mapped with one bounded offset, never per-turn resets."""

LABELS = {
    "asr_request": "ASR",
    "guardrail_embedding": "护栏",
    "memory_request": "Memory",
    "memory_writeback": "Memory",
    "llm_request": "LLM",
    "tts_request": "TTS",
    "tool_call": "工具 / RAG",
}


def build_session_chain(report):
    playback = report.get("session_playback", {})
    zero = playback.get("zero_at_ns")
    clients = list(report.get("startup", {}).get("events", []))
    servers = list(report.get("session_vas_events", []))
    owners = {}
    for index, turn in enumerate(report.get("turns", []), 1):
        clients.extend(dict(e, turn_index=index) for e in turn.get("events", []))
        servers.extend(turn.get("vas_events", []))
        owners[turn.get("server_listen_turn_id", turn.get("listen_turn_id", index))] = (
            index
        )
    clocks = {e.get("clock_id") for e in servers if e.get("monotonic_ns") is not None}
    result = {
        "status": "unavailable",
        "zero_at_ns": zero,
        "lanes": [],
        "client_lanes": [],
        "turn_count": len(report.get("turns", [])),
        "alignment": {"status": "unavailable"},
    }
    if zero is None:
        return result
    sec = lambda value: (value - zero) / 1e9
    # Control round-trip bounds: send_client <= receive_server + offset;
    # send_server + offset <= receive_client. One constant preserves all gaps.
    lower, upper = [], []
    sent = {
        (
            e["event"],
            e.get("data", {}).get(
                "server_listen_turn_id", e.get("data", {}).get("listen_turn_id")
            ),
        ): e["at_ns"]
        for e in clients
        if e["event"] in {"listen_start_sent", "listen_stop_sent"}
    }
    received = {
        e.get("data", {}).get("audio_seq"): e["at_ns"]
        for e in clients
        if e["event"] == "audio_packet_received"
    }
    for event in servers:
        name = event["event"]
        if name in {"listen_start_received", "listen_stop_received"}:
            at = sent.get(
                (name.replace("_received", "_sent"), event.get("listen_turn_id"))
            )
            if at is not None:
                lower.append(at - event["monotonic_ns"])
        elif name == "audio_output_frame":
            at = received.get(event.get("data", {}).get("audio_seq"))
            if at is not None:
                upper.append(at - event["monotonic_ns"])
    if len(clocks) == 1 and lower and upper and max(lower) <= min(upper):
        lo, hi = max(lower), min(upper)
        offset = (lo + hi) // 2
        result["alignment"] = {
            "status": "bounded",
            "method": "wire_causality_interval",
            "offset_ns": offset,
            "lower_ns": lo,
            "upper_ns": hi,
            "uncertainty_ms": (hi - lo) / 2e6,
            "clock_id": next(iter(clocks)),
            "anchor_count": len(lower) + len(upper),
        }
        result["status"] = "ready"
        starts = {}
        lanes = {}
        last = max(e["monotonic_ns"] for e in servers)
        for event in sorted(servers, key=lambda e: e["monotonic_ns"]):
            name = event["event"]
            if name.endswith("_started") and name.removesuffix("_started") in LABELS:
                starts[(name.removesuffix("_started"), event.get("span_id"))] = event
        ends = {
            (e["event"].removesuffix("_finished"), e.get("span_id")): e
            for e in servers
            if e["event"].endswith("_finished")
        }
        for (name, span_id), start in starts.items():
            end = ends.get((name, span_id))
            end_ns = end["monotonic_ns"] if end else last
            if end_ns < start["monotonic_ns"]:
                continue
            label = LABELS[name]
            lane = lanes.setdefault(
                label, {"label": label, "category": name, "segments": []}
            )
            owner = start.get(
                "client_turn_index", owners.get(start.get("listen_turn_id"), 0)
            )
            lane["segments"].append(
                {
                    "turn_index": owner,
                    "label": name,
                    "span_id": span_id,
                    "start_seconds": sec(start["monotonic_ns"] + offset),
                    "end_seconds": sec(end_ns + offset),
                    "duration_seconds": (
                        (end_ns - start["monotonic_ns"]) / 1e9 if end else None
                    ),
                    "status": end.get("status") if end else "unfinished",
                    "data": start.get("data", {}),
                    "server_start_ns": start["monotonic_ns"],
                    "server_end_ns": end_ns if end else None,
                }
            )
        order = list(dict.fromkeys(LABELS.values()))
        result["lanes"] = sorted(
            lanes.values(), key=lambda lane: order.index(lane["label"])
        )
    else:
        result["alignment"][
            "reason"
        ] = "缺少双向对齐证据，或时钟边界冲突；未将 VAS 时钟冒充客户端时钟。"
    control_names = {
        "connection_started": "开始连接",
        "connection_opened": "连接成功",
        "hello_received": "握手完成",
        "greeting_ready": "问候播放完成 / 输入就绪",
        "listen_start_sent": "开始输入",
        "listen_stop_sent": "输入结束",
        "stt": "识别返回",
        "tts_start": "回复开始",
        "tts_stop": "服务端发送结束",
        "abort_sent": "打断",
        "playback_drained": "播放结束",
    }
    controls = [
        {
            "turn_index": e.get("turn_index", 0),
            "label": control_names[e["event"]],
            "start_seconds": sec(e["at_ns"]),
            "end_seconds": sec(e["at_ns"]),
            "data": e.get("data", {}),
        }
        for e in clients
        if e["event"] in control_names
    ]
    network = []
    for index in range(len(report.get("turns", [])) + 1):
        group = [
            e
            for e in clients
            if e.get("turn_index", 0) == index and e["event"] == "audio_packet_received"
        ]
        if group:
            network.append(
                {
                    "turn_index": index,
                    "label": "下行音频接收",
                    "start_seconds": sec(group[0]["at_ns"]),
                    "end_seconds": sec(group[-1]["at_ns"]),
                    "data": {"frames": len(group)},
                }
            )
    result["client_lanes"] = [
        {"label": "下行收包", "segments": network},
        {"label": "客户端事件", "segments": controls},
    ]
    return result
