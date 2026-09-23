"""One presentation axis with optional, explicit clock calibration."""

import copy

from .reply_timing import playback_clock
from .session_chain import build_session_chain
from .timeline import group_timeline_spans, request_spans
from .tts_evidence import tts_segment_evidence

CLIENT_LABELS = {
    "connection_started": ("control", "客户端开始尝试连接"),
    "connection_opened": ("control", "WebSocket 连接成功"),
    "sensor_sent": ("control", "传感器指令发出"),
    "input_started": ("input", "开始发送输入"),
    "first_audio_sent": ("input", "第一帧音频发出"),
    "audio_send_completed": ("input", "音频发送完成"),
    "speech_input_started": ("input", "语音开始"),
    "speech_input_finished": ("input", "语音结束"),
    "background_noise_started": ("input", "持续发送底噪"),
    "listen_stop_sent": ("input", "停止指令发出"),
    "playback_started": ("playback", "开始播放回复"),
    "playback_stopped": ("playback", "停止播放"),
    "playback_drained": ("playback", "回复播放完成"),
    "abort_wire_sent": ("control", "打断指令发出"),
    "music_call_accepted": ("playback", "音乐指令已接受"),
    "music_playback_started": ("playback", "音乐开始播放"),
    "music_playback_stopped": ("playback", "音乐停止播放"),
}


def client_wall_time(at_ns, clock):
    """Project this process's monotonic time from its own session-start clock pair."""
    if not clock or not all(
        isinstance(clock.get(key), int) for key in ("monotonic_ns", "wall_time_ns")
    ):
        return None
    return clock["wall_time_ns"] + at_ns - clock["monotonic_ns"]


def vad_display_times(events):
    """Derive historical VAD wall position from its own endpoint clock only."""
    result = {}
    pending = {}
    for event in sorted(events, key=lambda e: e.get("monotonic_ns", 0)):
        key = (event.get("clock_id"), event.get("listen_turn_id"))
        if event.get("event") == "local_vad_last_voice":
            pending[key] = event
        elif event.get("event") == "local_vad_endpoint_detected" and key in pending:
            last = pending.pop(key)
            delta = event["monotonic_ns"] - last["monotonic_ns"]
            if 0 <= delta and isinstance(event.get("wall_time_ns"), int):
                derived = event["wall_time_ns"] - delta
                if abs(derived - last.get("wall_time_ns", derived)) > 1_000_000:
                    result[(key[0], last["monotonic_ns"])] = derived
    return result


def combined_timeline(turn, client_clock, clock_sync=None):
    calibrated = (clock_sync or {}).get("status") == "calibrated"
    offset = clock_sync["offset_ns"] if calibrated else 0
    client = {}
    unreliable_playback = playback_clock(turn)["status"] in {"invalid", "estimated"}
    group_labels = {"input": "发送音频", "playback": "回复播放", "control": "控制指令"}
    for event in turn.get("events", []):
        if event["event"] not in CLIENT_LABELS:
            continue
        if unreliable_playback and event["event"] == "playback_started":
            continue
        group, label = CLIENT_LABELS[event["event"]]
        lane = client.setdefault(
            group,
            dict(label=group_labels[group], category=group, segments=[], markers=[]),
        )
        lane["markers"].append(
            dict(
                label=label,
                event=event["event"],
                start_ns=event["at_ns"],
                data=event.get("data", {}),
                wall_time_ns=event.get("wall_time_ns"),
            )
        )
    if turn.get("media_markers"):
        client["media"] = dict(
            label="图片 / 视频到达",
            category="media",
            segments=[],
            markers=copy.deepcopy(turn["media_markers"]),
        )
    lanes = [dict(lane, source="client") for lane in client.values()] + [
        dict(copy.deepcopy(lane), source="server")
        for lane in turn.get("timeline_lanes", [])
    ]
    server_times = {
        (event.get("clock_id"), event.get("monotonic_ns")): event.get("wall_time_ns")
        for event in turn.get("vas_events", [])
    }
    derived_times = vad_display_times(turn.get("vas_events", []))
    server_times.update(derived_times)
    items = []
    for lane in lanes:
        for item in lane["segments"] + lane["markers"]:
            domain = (lane["source"], item.get("clock_id"))
            if domain[0] == "server" and (domain[1], item["start_ns"]) in derived_times:
                item["position_note"] = (
                    "位置经过换算：使用同一 VAS 时钟的结束事件与时间差，原始数据未改动"
                )

            def wall(at):
                if lane["source"] == "client":
                    return item.get("wall_time_ns") or client_wall_time(
                        at, client_clock
                    )
                value = server_times.get((domain[1], at))
                if calibrated:
                    item["clock_calibration"] = {
                        k: clock_sync[k] for k in ("offset_ns", "uncertainty_ns")
                    }
                return value - offset if isinstance(value, int) else value

            items.append(
                (
                    item,
                    domain,
                    wall(item["start_ns"]),
                    wall(item["end_ns"]) if "end_ns" in item else None,
                )
            )

    # Old recordings cannot recover client wall time from a monotonic counter.
    # Keep both sources visible but give every independent clock its own zero.
    complete = bool(items) and all(
        isinstance(start, int) and ("end_ns" not in item or isinstance(end, int))
        for item, _, start, end in items
    )
    discontinuity = complete and any(
        end is not None
        and abs((end - start) - (item["end_ns"] - item["start_ns"])) > 250_000_000
        for item, _, start, end in items
    )
    absolute = complete and not discontinuity
    client_origin = client_wall_time(
        (client_clock or {}).get("monotonic_ns", 0), client_clock
    )
    zero = (
        (
            client_origin
            if client_origin is not None
            else min((start for _, _, start, _ in items), default=0)
        )
        if absolute
        else None
    )
    local_zeros = {}
    for item, domain, _, _ in items:
        local_zeros[domain] = min(
            local_zeros.get(domain, item["start_ns"]), item["start_ns"]
        )
    for item, domain, start, end in items:
        item["wall_time_ms"] = start / 1e6 if start is not None else None
        item["end_wall_time_ms"] = end / 1e6 if end is not None else None
        item["plot_start_ns"] = (
            start - client_origin
            if domain[0] == "client" and client_origin is not None
            else start - zero if absolute else item["start_ns"] - local_zeros[domain]
        )
        if "end_ns" in item:
            item["plot_end_ns"] = (
                end - client_origin
                if domain[0] == "client" and client_origin is not None
                else end - zero if absolute else item["end_ns"] - local_zeros[domain]
            )
    return {
        "clock_sync": clock_sync,
        "mode": "wall" if absolute else "relative",
        "reason": (
            None
            if absolute
            else "clock_discontinuity" if discontinuity else "missing_wall_time"
        ),
        "time_zone": "Asia/Shanghai",
        "origin_wall_time_ms": zero / 1e6 if absolute else None,
        "origin_wall_time_ns": zero,
        "lanes": lanes,
    }


def session_trace_timeline(report, use_packet_alignment=True):
    """Retain every turn's lane structure on the unchanged session playback scale."""
    playback = report.get("session_playback", {})
    zero = playback.get("zero_at_ns")
    alignment = None
    if (
        use_packet_alignment
        and isinstance(zero, int)
        and (report.get("clock_sync") or {}).get("status") != "calibrated"
    ):
        candidate = build_session_chain(report)["alignment"]
        if candidate["status"] == "bounded":
            alignment = candidate
    wall_zero = (
        client_wall_time(zero, report.get("client_clock")) if zero is not None else None
    )
    sources = [(index, turn) for index, turn in enumerate(report.get("turns", []), 1)]
    session_events = report.get("session_events", [])
    if session_events:
        spans, _ = request_spans(session_events)
        texts = tts_segment_evidence(
            spans, session_events, report.get("startup", {}).get("events", [])
        )
        for span in spans:
            if span["span_id"] in texts:
                span["tts_evidence"] = texts[span["span_id"]]
        sources.insert(
            0,
            (
                None,
                {
                    "timeline_lanes": group_timeline_spans(spans, session_events),
                    "vas_events": session_events,
                },
            ),
        )
    charts = []
    for index, turn in sources:
        # Client speech/playback is already represented by the session's top tracks.
        chart = combined_timeline(
            {
                "timeline_lanes": turn.get("timeline_lanes", []),
                "vas_events": turn.get("vas_events", []),
            },
            None,
            report.get("clock_sync"),
        )
        if chart["lanes"]:
            charts.append((index, chart))
    absolute = (
        alignment is None
        and wall_zero is not None
        and all(chart["mode"] == "wall" for _, chart in charts)
    )
    local_zeros = {}
    for _, chart in charts:
        for lane in chart["lanes"]:
            for item in lane["segments"] + lane["markers"]:
                key = item.get("clock_id")
                local_zeros[key] = min(
                    local_zeros.get(key, item["start_ns"]), item["start_ns"]
                )
    lanes, bounds = [], [0, playback.get("duration_seconds", 0)]
    for index, chart in charts:
        for lane in chart["lanes"]:
            lane["turn_index"] = index
            for item in lane["segments"] + lane["markers"]:
                item["turn_index"] = index
                for key in ("start", "end"):
                    if f"{key}_ns" not in item:
                        continue
                    item[f"plot_{key}_ns"] = (
                        item[f"{key}_ns"] + alignment["offset_ns"] - zero
                        if alignment and item.get("clock_id") == alignment["clock_id"]
                        else (
                            item[f"plot_{key}_ns"]
                            + chart["origin_wall_time_ns"]
                            - wall_zero
                            if absolute
                            else item[f"{key}_ns"] - local_zeros[item.get("clock_id")]
                        )
                    )
                    bounds.append(item[f"plot_{key}_ns"] / 1e9)
            lanes.append(lane)
    # Media received outside the audio file's extent is still inspectable.
    bounds.extend(m["at_seconds"] for m in playback.get("media_markers", []))
    return {
        "clock_sync": report.get("clock_sync"),
        "alignment": alignment,
        "mode": "bounded" if alignment else "wall" if absolute else "relative",
        "time_zone": "Asia/Shanghai",
        "origin_wall_time_ms": wall_zero / 1e6 if absolute else None,
        "axis_start_seconds": min(bounds),
        "axis_end_seconds": max(bounds),
        "lanes": lanes,
    }
