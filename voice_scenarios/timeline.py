"""Presentation lanes that retain each request's original timing and identity."""

import math


def media_timeline_markers(events):
    """Media URL arrival is a client event, not server send/load/playback time."""
    markers = []
    labels = {"image": "图片", "video": "视频"}
    counts = dict.fromkeys(labels, 0)
    for event in events:
        at = event.get("at_ns")
        if (
            event.get("event") not in ("image", "display")
            or type(at) not in (int, float)
            or not math.isfinite(at)
        ):
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        items = (
            [{"kind": "image", "url": data.get("url")}]
            if event["event"] == "image"
            else data.get("items")
        )
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("kind") not in ("image", "video"):
                continue
            kind = item["kind"]
            counts[kind] += 1
            url = item.get("url")
            markers.append(
                {
                    "label": f"{labels[kind]} #{counts[kind]} 到达",
                    "kind": kind,
                    "start_ns": at,
                    "url": url if isinstance(url, str) else None,
                }
            )
    return markers


MILESTONE_LABELS = {
    "llm_request_started": "LLM 请求提交",
    "llm_first_token": "First Token",
    "llm_first_sse": "首个完整 SSE 数据块",
    "llm_first_output": "首个有效 SSE 增量",
    "llm_usage": "Token / 缓存用量",
    "http_request_finished": "HTTP 请求结束",
    "http_tcp_started": "TCP 开始",
    "http_tcp_finished": "TCP 完成",
    "http_tls_started": "TLS 开始",
    "http_tls_finished": "TLS 完成",
    "http_transport_error": "HTTP 传输失败",
    "http_transport_retry_started": "传输重试等待",
    "http_transport_retry_finished": "传输重试继续",
    "tts_first_text": "首个播报文本",
    "tts_text_complete": "文本结束提交",
    "tts_segment_ready": "片段可合成",
    "tts_request_started": "TTS 请求开始",
    "tts_first_pcm": "首个有效音频",
    "http_session_created": "HTTP 会话创建",
    "http_request_started": "HTTP 请求开始",
    "http_pool_wait_started": "等待连接池",
    "http_pool_wait_finished": "取得连接池名额",
    "http_connection_started": "开始建连",
    "http_connection_ready": "连接建立",
    "http_connection_reused": "复用连接",
    "http_dns_started": "DNS 开始",
    "http_dns_finished": "DNS 完成",
    "http_request_headers_sent": "请求头发送",
    "http_request_body_sent": "请求体发送",
    "http_response_headers": "响应头到达",
    "http_request_error": "HTTP 请求失败",
    "http_request_redirected": "HTTP 重定向",
    "listen_stop_received": "VAS 收到停止",
    "listen_stop_enqueued": "停止消息入队",
    "listen_stop_dequeued": "停止消息出队",
    "listen_finalize_started": "开始处理语音结束",
    "audio_input_completed": "上行音频接收完成",
    "asr_request_started": "ASR 请求开始",
    "asr_connection_opened": "ASR 连接建立",
    "asr_connection_closed": "ASR 连接关闭",
    "asr_session_config_requested": "ASR VAD 请求设置",
    "asr_session_config_confirmed": "ASR VAD 服务端确认",
    "asr_first_audio_sent": "ASR 首帧提交",
    "asr_commit_sent": "ASR 提交结束",
    "asr_partial": "ASR 首个中间结果",
    "asr_final": "ASR 最终识别结果",
    "asr_speech_started": "ASR VAD 语音开始",
    "asr_endpoint_detected": "ASR VAD 结束",
    "local_vad_speech_started": "本地 VAD 语音开始",
    "local_vad_last_voice": "本地 VAD 最后语音帧",
    "local_vad_endpoint_detected": "本地 VAD 结束",
    "guardrail_released": "护栏放行",
    "guardrail_blocked": "护栏拦截",
    "guardrail_cancelled": "护栏取消",
    "guardrail_embedding_cache": "护栏向量缓存",
    "guardrail_embedding_wait_started": "等待规则向量",
    "guardrail_embedding_wait_finished": "规则向量等待结束",
}


def group_timeline_spans(spans, events):
    ordered = sorted(spans, key=lambda span: span["start_ns"])
    nodes = {span["span_id"]: span for span in ordered}
    starts = {
        event["span_id"]: event
        for event in events
        if event.get("span_id") in nodes
        and event["event"] == nodes[event["span_id"]]["name"] + "_started"
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
    span_lanes = {}
    speech_spans = {}
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
        elif name == "asr_request":
            key = ("asr",)
            if span.get("data", {}).get("mode") not in (None, "STREAM"):
                label = "ASR 识别请求"
        elif name == "guardrail_embedding":
            key = ("guardrail",)
            label = "护栏 Embedding"
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
            segment_id = span.get("data", {}).get("segment_id")
            if segment_id:
                speech_spans.setdefault(segment_id, span)
        if key not in lanes:
            lanes[key] = dict(
                label=label, category=name, llm_span_id=owner, segments=[], markers=[]
            )
        lanes[key]["segments"].append(segment)
        span_lanes[span["span_id"]] = lanes[key]

    seen_partial = set()
    for event in sorted(events, key=lambda e: e.get("monotonic_ns", 0)):
        name = event["event"]
        at = event.get("monotonic_ns")
        if name not in MILESTONE_LABELS or at is None:
            continue
        request = nodes.get(event.get("span_id"))
        if name == "tts_segment_ready":
            request = speech_spans.get(event.get("data", {}).get("segment_id"), request)
        if (
            request
            and request.get("clock_id")
            and event.get("clock_id") != request["clock_id"]
        ):
            continue
        lane = span_lanes.get(request["span_id"]) if request else None
        if lane is None and name == "tts_text_complete" and event.get("output_id"):
            matches = [
                candidate
                for candidate in lanes.values()
                if candidate["category"] == "tts_request"
                and any(
                    segment.get("output_id") == event["output_id"]
                    and (
                        not segment.get("clock_id")
                        or segment["clock_id"] == event.get("clock_id")
                    )
                    for segment in candidate["segments"]
                )
            ]
            if len(matches) == 1:
                lane = matches[0]
        if name.startswith("guardrail_"):
            lane = lanes.setdefault(
                ("guardrail",),
                dict(
                    label="护栏 Embedding",
                    category="guardrail_embedding",
                    segments=[],
                    markers=[],
                ),
            )
        elif lane is None and name.startswith(
            ("asr_", "local_vad_", "listen_", "audio_input_")
        ):
            lane = lanes.setdefault(
                ("asr",),
                dict(
                    label="ASR / 输入结束",
                    category="asr_request",
                    segments=[],
                    markers=[],
                ),
            )
        if lane is None:
            # Keep evidence from cancelled/incomplete requests without guessing a
            # neighbouring request. Each orphan retains its own span identity.
            key = ("unmatched", event.get("span_id"))
            lane = lanes.setdefault(
                key,
                dict(
                    label="未完成 / 未关联请求",
                    category="unmatched",
                    segments=[],
                    markers=[],
                ),
            )
        partial_key = event.get("span_id")
        if name == "asr_partial":
            if partial_key in seen_partial:
                continue
            seen_partial.add(partial_key)
        marker = dict(
            event=name,
            label=MILESTONE_LABELS[name],
            start_ns=at,
            span_id=event.get("span_id"),
            data=event.get("data", {}),
            status=event.get("status"),
            since_request_seconds=None,
        )
        if request and at >= request["start_ns"]:
            marker["since_request_seconds"] = (at - request["start_ns"]) / 1e9
        lane["markers"].append(marker)
    for lane in lanes.values():
        if lane["category"] == "tts_request":
            lane["label"] += f" · {len(lane['segments'])} 段"
    return list(lanes.values())
