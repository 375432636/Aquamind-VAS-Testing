"""Join RAG query/result fragments only to the originating tool invocation."""

LABELS = {
    "returned": "已返回",
    "empty": "空返回",
    "error": "调用失败",
    "cancelled": "已取消",
    "unavailable": "结果未采集",
}


def knowledge_evidence(events):
    results, fragments, endings, conflicts = {}, {}, {}, set()
    for event in events:
        data = event.get("data", {})
        key = event.get("clock_id"), event.get("span_id")
        if not key[1]:
            continue
        name = event.get("event")
        if name == "tool_call_started" and data.get("category") == "knowledge_base":
            results.setdefault(key, {"label": "结果未采集"})
        elif name == "knowledge_lookup_result":
            results[key] = dict(
                data, label=LABELS.get(data.get("retrieval_status"), "结果未采集")
            )
        kind = data.get("content_kind")
        if kind not in {"query", "returned"}:
            continue
        content_key = (*key, kind)
        if name == "knowledge_context_part":
            index, text = data.get("part_index"), data.get("text")
            if type(index) is int and 0 <= index < 128 and isinstance(text, str):
                parts = fragments.setdefault(content_key, {})
                if index in parts and parts[index] != text:
                    conflicts.add(content_key)
                parts.setdefault(index, text)
        elif name == "knowledge_context_complete":
            if content_key in endings and endings[content_key] != data:
                conflicts.add(content_key)
            endings[content_key] = data
    for key, result in results.items():
        for kind in ("query", "returned"):
            content_key = (*key, kind)
            parts, end = fragments.get(content_key, {}), endings.get(content_key, {})
            count = end.get("part_count")
            text = "".join(parts[i] for i in sorted(parts))
            complete = (
                type(count) is int
                and 0 <= count <= 128
                and set(parts) == set(range(count))
                and content_key not in conflicts
                and (
                    end.get("captured_bytes") is None
                    or len(text.encode("utf-8")) == end["captured_bytes"]
                )
            )
            result[kind] = dict(
                text=text,
                complete=complete,
                available=end.get("available", False),
                truncated=end.get("truncated", False),
            )
    return results
