"""Associate mixed inputs using ordered TTS controls, never cross-clock guesses.

Legacy VAS increments listen_turn_id only for listen/start. Sensor turns reuse
the last value. Raw records retain that ID; client_turn_index is report-only.
"""

from collections import defaultdict
from bisect import bisect_right


def attribute_text_turns(report, events):
    """Map native text turns when VAS leaves listen_turn_id empty for detect."""

    turns = report.get("turns", [])
    if not turns or any(turn.get("input_type") != "text" for turn in turns):
        return events, []
    detects = [row for row in events if row.get("event") == "listen_detect_received"]
    if not detects and any(row.get("listen_turn_id") is not None for row in events):
        # Browser sessions can already provide verified server turn IDs.
        return events, []
    if detects and all(row.get("listen_turn_id") is not None for row in detects):
        # A future VAS may label native text turns itself.
        return events, []
    sent = [
        sum(row.get("event") == "text_sent" for row in turn.get("events", []))
        for turn in turns
    ]
    if (
        len(detects) != len(turns)
        or any(count != 1 for count in sent)
        or any(row.get("listen_turn_id") is not None for row in detects)
        or any(type(row.get("seq")) is not int for row in events)
    ):
        return events, [
            "文字会话的 listen/detect 诊断与客户端轮次不匹配，无法可靠归属 robot_output。"
        ]
    boundaries = [row["seq"] for row in detects]
    if boundaries != sorted(set(boundaries)):
        return events, ["文字会话的 listen/detect 诊断顺序无效，无法可靠归属。"]
    return [
        dict(row, client_turn_index=bisect_right(boundaries, row["seq"]))
        for row in events
    ], []


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


def attribute_reused_listen_turns(report, events):
    """Split one continuous VAD listen stream into its browser report turns.

    The browser keeps one ``listen/start`` and one recorder alive while VAS
    creates several utterances. Raw diagnostics therefore reuse one
    ``listen_turn_id``. ``speech_chunk_started`` provides an ordered,
    clock-independent boundary and ``utterance_id`` keeps late/cancelled spans
    with the utterance that created them.
    """

    if any(turn.get("sensor") for turn in report.get("turns", [])):
        return events, []
    groups = defaultdict(list)
    for index, turn in enumerate(report.get("turns", []), 1):
        raw_id = turn.get("server_listen_turn_id")
        if raw_id is not None:
            groups[raw_id].append(index)
    reused = {raw_id: owners for raw_id, owners in groups.items() if len(owners) > 1}
    if not reused:
        return events, []

    rows = [dict(event, client_turn_index=0) for event in events]
    failures = []
    for raw_id, owners in groups.items():
        matching = [row for row in rows if row.get("listen_turn_id") == raw_id]
        if len(owners) == 1:
            for row in matching:
                row["client_turn_index"] = owners[0]
            continue

        starts, seen = [], set()
        for row in matching:
            if row.get("event") != "speech_chunk_started":
                continue
            utterance_id = row.get("data", {}).get("utterance_id")
            if utterance_id and utterance_id not in seen:
                seen.add(utterance_id)
                starts.append((row["seq"], utterance_id))
        if len(starts) != len(owners):
            failures.append(
                f"连续 VAD 诊断包含 {len(starts)} 个语句边界，但客户端记录了 {len(owners)} 个轮次；原始数据已保留。"
            )
        usable = min(len(starts), len(owners))
        utterance_owners = {
            utterance_id: owner
            for (_, utterance_id), owner in zip(starts[:usable], owners[:usable])
        }
        bounds = [
            (seq, owner) for (seq, _), owner in zip(starts[:usable], owners[:usable])
        ]
        span_owners, output_owners = {}, {}
        for row in matching:
            data = row.get("data", {})
            explicit = utterance_owners.get(data.get("utterance_id"))
            owner = explicit or owners[0]
            if not explicit:
                for seq, candidate in bounds:
                    if row["seq"] >= seq:
                        owner = candidate
            span = row.get("span_id")
            parent = row.get("parent_span_id")
            output = row.get("output_id")
            if not explicit:
                owner = span_owners.get(
                    span, output_owners.get(output, span_owners.get(parent, owner))
                )
            if span:
                span_owners.setdefault(span, owner)
            if output:
                output_owners.setdefault(output, owner)
            row["client_turn_index"] = owner
    return rows, failures
