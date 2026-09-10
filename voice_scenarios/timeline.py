"""Presentation lanes that retain each request's original timing and identity."""


def group_timeline_spans(spans, events):
    ordered = sorted(spans, key=lambda span: span["start_ns"])
    nodes = {span["span_id"]: span for span in ordered}
    starts = {
        event["span_id"]: event
        for event in events
        if event.get("span_id") and event["event"].endswith("_started")
    }
    llm_numbers = {
        span["span_id"]: index
        for index, span in enumerate(
            (span for span in ordered if span["name"] == "llm_request"), 1
        )
    }

    def llm_parent(span):
        parent = starts.get(span["span_id"], {}).get("parent_span_id") or span.get(
            "parent_span_id"
        )
        visited = set()
        while parent and parent not in visited:
            if parent in llm_numbers:
                return parent
            visited.add(parent)
            parent = starts.get(parent, {}).get("parent_span_id") or nodes.get(
                parent, {}
            ).get("parent_span_id")
        return None

    lanes = {}
    for span in ordered:
        event = starts.get(span["span_id"], {})
        segment = dict(
            span,
            output_id=event.get("output_id"),
            output_kind=event.get("output_kind"),
        )
        name = span["name"]
        owner = llm_parent(span) if name == "tts_request" else None
        key = ("span", span["span_id"])
        label = "流式 ASR 全程（含音频上传）" if name == "asr_request" else name
        if name == "llm_request":
            label = f"LLM #{llm_numbers[span['span_id']]}"
        elif name == "tool_call":
            label = span.get("data", {}).get("tool_name", "工具调用")
        elif name == "tts_request":
            parent = event.get("parent_span_id") or span.get("parent_span_id")
            if owner:
                key = ("llm", owner)
                label = f"LLM #{llm_numbers[owner]} 的 TTS"
            elif parent:
                key = ("parent", parent)
                label = "TTS · 同一父请求"
            elif event.get("output_id"):
                key = ("output", event["output_id"])
                kind = {"pre_speech": "过渡语", "answer": "正式回答"}.get(
                    event.get("output_kind"), "同一输出"
                )
                label = f"TTS · {kind}"
            else:
                label = "TTS · 未关联片段"
        if key not in lanes:
            lanes[key] = dict(
                label=label, category=name, llm_span_id=owner, segments=[]
            )
        lanes[key]["segments"].append(segment)
    for lane in lanes.values():
        if lane["category"] == "tts_request":
            lane["label"] += f" · {len(lane['segments'])} 段"
    return list(lanes.values())
