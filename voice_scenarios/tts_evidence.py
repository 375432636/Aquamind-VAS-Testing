"""Conservative links from TTS requests to received sentence-message text.

The text is a client-visible playback annotation, not the exact provider input.
Neither receipt times nor text length alone establish a request's identity.
"""

from collections import defaultdict


def _server_scope(event):
    if not event.get("server_session_id") or "listen_turn_id" not in event:
        return None
    return event["server_session_id"], event["listen_turn_id"]


def _client_scope(data):
    if not data.get("session_id") or "response_listen_turn_id" not in data:
        return None
    return data["session_id"], data["response_listen_turn_id"]


def _sentences(events):
    groups = defaultdict(list)
    active = None
    chunks = any(e.get("event") == "input_chunk_finished" for e in events)
    for event in events:
        name, data = event.get("event"), event.get("data", {})
        if name == "tts_sentence_start":
            scope = _client_scope(data)
            active = None
            if scope is not None and isinstance(data.get("text"), str):
                active = dict(
                    event=event, text=data["text"], audio_seqs=[], scope=scope
                )
                groups[scope].append(active)
        elif name in {"tts_sentence_end", "tts_stop"} or (
            name == "listen_start_sent" and not chunks
        ):
            active = None
        elif name == "audio_packet_received" and active is not None:
            seq = data.get("audio_seq")
            if type(seq) is int:
                active["audio_seqs"].append(seq)
    return groups


def _segment_ready(start, events):
    segment = start.get("data", {}).get("segment_id")
    candidates = [
        event
        for event in events
        if segment
        and event.get("event") == "tts_segment_ready"
        and event.get("data", {}).get("segment_id") == segment
        and _server_scope(event) == _server_scope(start)
        and event.get("clock_id") == start.get("clock_id")
        and event.get("output_id") == start.get("output_id")
    ]
    return candidates[0] if len(candidates) == 1 else None


def _cached_pairs(vas_events, client_events):
    """Verify prerecorded filler by complete sentence order and exact packets."""
    sentences = _sentences(client_events)
    states = defaultdict(list)
    for event in vas_events:
        if (
            event.get("event") == "tts_state_sent"
            and event.get("data", {}).get("state") == "sentence_start"
        ):
            states[_server_scope(event)].append(event)
    pairs = []
    for scope, rows in states.items():
        client = sentences.get(scope, [])
        if (
            scope is None
            or len(rows) != len(client)
            or any(type(row.get("seq")) is not int for row in rows)
        ):
            continue
        rows = sorted(rows, key=lambda row: row["seq"])
        if len({row["seq"] for row in rows}) != len(rows):
            continue
        for index, (state, sentence) in enumerate(zip(rows, client)):
            if (
                state.get("span_id") is not None
                or state.get("output_kind") != "filler"
                or not state.get("output_id")
            ):
                continue
            end = rows[index + 1]["seq"] if index + 1 < len(rows) else float("inf")
            packets = [
                event.get("data", {}).get("audio_seq")
                for event in vas_events
                if event.get("event") == "audio_output_frame"
                and _server_scope(event) == scope
                and event.get("clock_id") == state.get("clock_id")
                and event.get("output_id") == state["output_id"]
                and state["seq"] < event.get("seq", -1) < end
            ]
            if (
                packets
                and all(type(seq) is int for seq in packets)
                and packets == sentence["audio_seqs"]
                and len(set(packets)) == len(packets)
            ):
                pairs.append((state, sentence))
    return pairs


def cached_audio_annotations(vas_events, client_events):
    """Annotate cached filler without inventing a TTS synthesis request."""
    return {
        str(seq): {
            "output_kind": "filler",
            "output_id": state["output_id"],
            "source": "cached_sentence_audio_sequence",
            "span_id": None,
        }
        for state, sentence in _cached_pairs(vas_events, client_events)
        for seq in sentence["audio_seqs"]
    }


def _protocol_pairs(states, sentences, starts, ready):
    """Only a complete, consistent ordered WS sentence exchange may be paired."""
    if not states or len(states) != len(sentences):
        return None, "服务端与客户端句消息数量不一致，不能按顺序关联"
    seqs = [event.get("seq") for event in states]
    if any(type(seq) is not int for seq in seqs) or len(set(seqs)) != len(seqs):
        return None, "服务端句消息顺序缺失或重复"
    states = sorted(states, key=lambda event: event["seq"])
    pairs = {}
    for state, sentence in zip(states, sentences):
        span_id = state.get("span_id")
        start = starts.get(span_id)
        segment = ready.get(span_id)
        if (
            start is None
            or segment is None
            or span_id in pairs
            or _server_scope(state) != _server_scope(start)
            or state.get("clock_id") != start.get("clock_id")
            or state.get("output_id") != start.get("output_id")
        ):
            return None, "句消息缺少唯一的 TTS 请求或片段标识"
        if segment.get("data", {}).get("text_chars") != len(sentence["text"]):
            return None, "逐句字符数与服务端片段记录不一致"
        client_output = sentence["event"].get("data", {}).get("output_id")
        if client_output is not None and client_output != start.get("output_id"):
            return None, "客户端句消息的输出标识与服务端不一致"
        pairs[span_id] = sentence
    return pairs, None


def tts_segment_evidence(spans, vas_events, client_events):
    """Return ``{tts_span_id: evidence}`` without mutating input records.

    Session and explicit turn identities are mandatory (null is the startup
    turn). Complete sentence-message order and each segment's character count
    validate packet ownership: old live clients may have dropped packets before
    incrementing their local audio sequence. A complete protocol exchange can
    still link text when those counters drift, with that fallback stated.

    ``segment_ready_ns`` is the existing TTS segment-ready event and
    ``text_received_ns`` is its existing queue-ingress observation. Neither is
    renamed to LLM generation time. Missing/ambiguous data remains unlinked.
    """
    by_span = defaultdict(list)
    for event in vas_events:
        if event.get("event") == "tts_request_started" and event.get("span_id"):
            by_span[event["span_id"]].append(event)
    starts = {key: rows[0] for key, rows in by_span.items() if len(rows) == 1}
    ready = {key: _segment_ready(start, vas_events) for key, start in starts.items()}
    sentences = _sentences(client_events)
    cached = _cached_pairs(vas_events, client_events)
    cached_states = {id(state) for state, _ in cached}
    cached_client = {id(sentence["event"]) for _, sentence in cached}
    sentences = {
        scope: [s for s in rows if id(s["event"]) not in cached_client]
        for scope, rows in sentences.items()
    }
    states = defaultdict(list)
    packets = defaultdict(set)
    for event in vas_events:
        scope = _server_scope(event)
        if scope is None:
            continue
        data = event.get("data", {})
        if (
            event.get("event") == "tts_state_sent"
            and data.get("state") == "sentence_start"
            and id(event) not in cached_states
        ):
            states[scope].append(event)
        if event.get("event") in {"audio_output_frame", "audio_output_started"}:
            seq, span_id = data.get("audio_seq"), event.get("span_id")
            start = starts.get(span_id)
            if (
                type(seq) is int
                and start
                and scope == _server_scope(start)
                and event.get("clock_id") == start.get("clock_id")
                and event.get("output_id") == start.get("output_id")
            ):
                packets[scope, seq].add(span_id)

    protocols = {
        scope: _protocol_pairs(rows, sentences.get(scope, []), starts, ready)
        for scope, rows in states.items()
    }
    output = {}
    for span in spans:
        if span.get("name") != "tts_request" or not span.get("span_id"):
            continue
        span_id = span["span_id"]
        evidence = dict(
            status="unmatched",
            text=None,
            source=None,
            source_label="未关联播报文本",
            reason="未收到该合成片段可可靠关联的句消息",
            notes=[],
            audio_seqs=[],
            segment_id=span.get("data", {}).get("segment_id"),
        )
        output[span_id] = evidence
        start = starts.get(span_id)
        if start is None:
            if len(by_span.get(span_id, [])) > 1:
                evidence.update(
                    status="ambiguous", reason="TTS 请求标识对应多个开始记录"
                )
            continue
        scope = _server_scope(start)
        if scope is None:
            evidence["reason"] = "TTS 请求缺少会话或轮次标识"
            continue
        segment = ready.get(span_id)
        if segment is not None:
            evidence.update(
                segment_id=segment.get("data", {}).get("segment_id"),
                segment_ready_ns=segment.get("monotonic_ns"),
                text_received_ns=segment.get("data", {}).get("text_received_ns"),
                clock_id=segment.get("clock_id"),
                trigger=segment.get("data", {}).get("trigger"),
            )
        pairs, reason = protocols.get(scope, (None, evidence["reason"]))
        if pairs is None:
            evidence["reason"] = reason
            if any(
                span_id in owners and len(owners) > 1
                for (packet_scope, _), owners in packets.items()
                if packet_scope == scope
            ):
                evidence.update(
                    status="ambiguous", reason="同一音频包序号对应多个 TTS 请求"
                )
            continue
        sentence = pairs.get(span_id)
        if sentence is None:
            continue
        seqs = sentence["audio_seqs"]
        packet_link = bool(seqs) and all(
            packets[scope, seq] == {span_id} for seq in seqs
        )
        source = "audio_sequence" if packet_link else "sentence_protocol_order"
        evidence.update(
            status="matched",
            text=sentence["text"],
            output_kind=start.get("output_kind"),
            output_id=start.get("output_id"),
            source=source,
            source_label=(
                "播报文本（按音频包关联）"
                if packet_link
                else "播报文本（按句消息顺序关联）"
            ),
            reason=None,
            audio_seqs=list(seqs),
            client_text_at_ns=sentence["event"].get(
                "at_ns", sentence["event"].get("monotonic_ns")
            ),
        )
        if not packet_link:
            evidence["notes"].append(
                "音频包序号不足、缺失或冲突；使用同一会话同一轮次的完整句消息顺序，已逐句核对字符数"
            )
    return output
