"""Associate mixed inputs using ordered TTS controls, never cross-clock guesses.

Legacy VAS increments listen_turn_id only for listen/start. Sensor turns reuse
the last value. Raw records retain that ID; client_turn_index is report-only.
"""

from collections import defaultdict


def attribute_mixed_turns(report, events):
    turns = report.get("turns", [])
    if not any(t.get("sensor") for t in turns):
        return events, []
    groups, audio_owners = defaultdict(list), {}
    listen_id = 0
    for index, turn in enumerate(turns, 1):
        if not turn.get("sensor"):
            listen_id += 1
            audio_owners[listen_id] = index
        turn["server_listen_turn_id"] = listen_id or None
        groups[listen_id or None].append(index)
    rows = [dict(e, client_turn_index=0) for e in events]
    if not rows:
        return rows, []
    server = [
        e
        for e in rows
        if e["event"] == "abort_stop_sent"
        or (
            e["event"] == "tts_state_sent"
            and e.get("data", {}).get("state") in {"start", "stop"}
        )
    ]
    wire = [
        e
        for t in [report.get("startup", {}), *turns]
        for e in t.get("events", [])
        if e["event"] in {"tts_start", "tts_stop"}
    ]
    if [
        "stop" if e["event"] == "abort_stop_sent" else e["data"]["state"]
        for e in server
    ] != [e["event"][4:] for e in wire]:
        return rows, [
            "传感器混合会话的 TTS 控制记录不一致，无法可靠关联内部诊断；原始数据已保留。"
        ]
    bounds, last_stops = {}, {}
    for source, received in zip(server, wire):
        data = received.get("data", {})
        owner = data.get("response_listen_turn_id")
        if data.get("is_session_output"):
            owner = 0
        if owner is None:
            return rows, ["传感器会话缺少客户端回复归属，无法关联内部诊断。"]
        seq = source["seq"]
        if received["event"] == "tts_stop":
            last_stops[owner] = seq
        elif owner and owner not in bounds:
            # Sequential scenarios send the next input after the preceding
            # reply drains. This exact server control also covers pre-STT work.
            bounds[owner] = last_stops.get(owner - 1, seq - 1) + 1
    span_owners, output_owners = {}, {}
    for row in rows:
        raw_id = row.get("listen_turn_id")
        candidates = groups.get(raw_id, [])
        owner = 0
        for candidate in candidates:
            if not turns[candidate - 1].get("sensor") or row["seq"] >= bounds.get(
                candidate, float("inf")
            ):
                owner = candidate
        if row["event"].startswith(("asr_", "local_vad_", "listen_", "audio_input_")):
            owner = audio_owners.get(raw_id, 0)
        span, parent, output = (
            row.get("span_id"),
            row.get("parent_span_id"),
            row.get("output_id"),
        )
        owner = span_owners.get(
            span, output_owners.get(output, span_owners.get(parent, owner))
        )
        if span:
            span_owners.setdefault(span, owner)
        if output:
            output_owners.setdefault(output, owner)
        row["client_turn_index"] = owner
    return rows, []
