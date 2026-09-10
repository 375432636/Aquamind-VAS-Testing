"""Offline timing analysis and regression verdicts from raw, separate clock domains."""

import copy
import json
import re
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import quote

from .reply_timing import analyze_reply_timing, input_end_event
from .timeline import group_timeline_spans


def delta(events, start, end, key):
    a = next((e[key] for e in events if e["event"] == start), None)
    b = next((e[key] for e in events if e["event"] == end), None)
    return (b - a) / 1e6 if a is not None and b is not None and b >= a else None


def evaluate(result, vas_events):
    report = copy.deepcopy(result)
    failures = []
    tts_capabilities = next(
        (
            e["data"]
            for e in reversed(vas_events)
            if e["event"] == "diagnostic_capabilities"
            and e.get("data", {}).get("component") == "tts"
        ),
        {},
    )
    report["metric_limitations"] = []
    if tts_capabilities.get("request_timing") is False:
        report["metric_limitations"].append(
            f"{tts_capabilities.get('provider', '当前 TTS')} 未接入内部诊断：合成请求次数、耗时及回复类型归属未采集；公共收发和播放时间仍可查看。"
        )
    if report.get("diagnostics") and not report["diagnostics"].get("complete"):
        failures.append("诊断数据不完整")
    output_edges = sorted(
        (e for e in vas_events if e["event"] == "audio_output_started"),
        key=lambda e: e["data"]["audio_seq"],
    )
    for index, turn in enumerate(report["turns"], 1):
        rows = [
            e
            for e in vas_events
            if e.get("listen_turn_id") == turn.get("listen_turn_id", index)
        ]
        turn["vas_events"] = rows
        for event in turn["events"]:
            audio_seq = event.get("data", {}).get("audio_seq")
            if audio_seq is not None and output_edges:
                edge = next(
                    (
                        e
                        for e in reversed(output_edges)
                        if e["data"]["audio_seq"] <= audio_seq
                    ),
                    None,
                )
                if edge:
                    event["data"].update(
                        server_listen_turn_id=edge.get("listen_turn_id"),
                        output_kind=edge.get("output_kind"),
                        output_id=edge.get("output_id"),
                    )
                    if (
                        event["event"] == "playback_frame_started"
                        and edge.get("listen_turn_id") is not None
                        and edge.get("listen_turn_id") != index
                    ):
                        failures.append(
                            f"{turn['id']}: 播放了属于其他轮次的迟到音频 seq={audio_seq}"
                        )
        starts = {
            (e.get("span_id"), e["event"].removesuffix("_started")): e
            for e in rows
            if e["event"].endswith("_started") and e.get("span_id")
        }
        spans = []
        for end in rows:
            start = starts.get(
                (end.get("span_id"), end["event"].removesuffix("_finished"))
            )
            if (
                start
                and end["event"]
                == start["event"].removesuffix("_started") + "_finished"
            ):
                if start["clock_id"] != end["clock_id"]:
                    failures.append("span 的时钟来源不一致")
                    continue
                spans.append(
                    {
                        "name": start["event"].removesuffix("_started"),
                        "start_ns": start["monotonic_ns"],
                        "end_ns": end["monotonic_ns"],
                        "duration_ms": (end["monotonic_ns"] - start["monotonic_ns"])
                        / 1e6,
                        "status": end.get("status"),
                        "data": start.get("data", {}),
                        "span_id": end.get("span_id"),
                        "parent_span_id": start.get("parent_span_id"),
                    }
                )
        turn["spans"] = spans
        counts = Counter(e["event"] for e in rows)
        tools = Counter(
            e["data"]["tool_name"] for e in rows if e["event"] == "tool_call_started"
        )
        metrics = {
            "asr_text": next(
                (
                    e.get("data", {}).get("text")
                    for e in turn["events"]
                    if e["event"] == "stt"
                ),
                None,
            ),
            "first_playback_ms": delta(
                turn["events"], input_end_event(turn), "playback_started", "at_ns"
            ),
            "stop_queue_ms": delta(
                rows, "listen_stop_enqueued", "listen_stop_dequeued", "monotonic_ns"
            ),
            "stop_to_execution_ms": delta(
                rows, "listen_stop_received", "listen_finalize_started", "monotonic_ns"
            ),
            "asr_commit_to_final_ms": delta(
                rows, "asr_commit_sent", "asr_final", "monotonic_ns"
            ),
            "local_vad_silence_ms": delta(
                rows,
                "local_vad_last_voice",
                "local_vad_endpoint_detected",
                "monotonic_ns",
            ),
            "asr_endpoint_to_final_ms": delta(
                rows, "asr_endpoint_detected", "asr_final", "monotonic_ns"
            ),
            "local_vad_starts": counts["local_vad_speech_started"],
            "local_vad_ends": counts["local_vad_endpoint_detected"],
            "asr_vad_starts": counts["asr_speech_started"],
            "asr_vad_ends": counts["asr_endpoint_detected"],
            "llm_requests": counts["llm_request_started"],
            "memory_requests": counts["memory_request_started"],
            "tts_requests": counts["tts_request_started"],
            "tools": dict(tools),
            "pre_speech_outputs": sum(
                e.get("output_kind") == "pre_speech"
                for e in rows
                if e["event"] == "audio_output_started"
            ),
            "filler_outputs": sum(
                e.get("output_kind") == "filler"
                for e in rows
                if e["event"] == "audio_output_started"
            ),
            "knowledge_base_calls": sum(
                e["data"].get("category") == "knowledge_base"
                for e in rows
                if e["event"] == "tool_call_started"
            ),
        }
        outputs = [e for e in rows if e["event"] == "audio_output_started"]
        played = {
            e["data"].get("audio_seq"): e["at_ns"]
            for e in turn["events"]
            if e["event"] == "playback_frame_started"
        }
        answer_ns = next(
            (
                played[e["data"]["audio_seq"]]
                for e in outputs
                if e.get("output_kind") == "answer" and e["data"]["audio_seq"] in played
            ),
            None,
        )
        stop_ns = next(
            (e["at_ns"] for e in turn["events"] if e["event"] == input_end_event(turn)),
            None,
        )
        metrics["first_answer_playback_ms"] = (
            (answer_ns - stop_ns) / 1e6
            if answer_ns is not None and stop_ns is not None
            else None
        )
        if tts_capabilities.get("request_timing") is False:
            metrics["tts_requests"] = None
        if tts_capabilities.get("output_attribution") is False:
            for name in (
                "pre_speech_outputs",
                "filler_outputs",
                "first_answer_playback_ms",
            ):
                metrics[name] = None
        interruption = turn.get("interruption")
        if interruption:
            metrics["interrupt_lateness_ms"] = interruption.get("trigger_lateness_ms")
            interruption["vas_abort_received"] = counts["abort_received"] > 0
            interruption["llm_abort_observed"] = counts["llm_abort_observed"] > 0
            interruption["tts_abort_observed"] = counts["tts_abort_observed"] > 0
            interruption["provider_cancellation"] = (
                "observed_locally"
                if interruption["llm_abort_observed"]
                or interruption["tts_abort_observed"]
                else "unobserved"
            )
            interruption["remote_provider_cancelled"] = "unknown"
        turn["metrics"] = metrics
        checks = []
        for name, expected in turn.get("expected", {}).items():
            if name == "tools":
                actual = dict(tools)
                passed = actual == expected
            elif name.startswith("max_"):
                actual = metrics.get(name[4:])
                passed = actual is not None and actual <= expected
            elif name.endswith("_min"):
                actual = metrics.get(name[:-4])
                passed = actual is not None and actual >= expected
            else:
                actual = metrics.get(name)
                passed = actual is not None and actual == expected
            checks.append(
                {"name": name, "expected": expected, "actual": actual, "passed": passed}
            )
            if not passed:
                failures.append(f"{turn['id']}: {name} 期望 {expected}，实际 {actual}")
        turn["checks"] = checks
        if turn["status"] == "failed":
            failures.append(f"{turn['id']}: {turn.get('error','运行失败')}")
    report["session_events"] = [
        e for e in vas_events if e.get("listen_turn_id") is None
    ]
    report["failures"] = failures
    if failures:
        report["status"] = "failed"
    return report


def _text(value):
    return escape(str(value if value is not None else "—"), quote=True)


def _seconds(value):
    return "—" if value is None else f"{value:.2f}"


def _question(turn):
    return (
        turn.get("input_text")
        or turn.get("metrics", {}).get("asr_text")
        or turn.get("id", "未命名轮次")
    )


def _turn_failed(turn):
    return turn.get("status") == "failed" or any(
        not check["passed"] for check in turn.get("checks", [])
    )


def _badge(label, tone="neutral"):
    return f'<span class="badge {tone}">{_text(label)}</span>'


def _turn_badges(turn):
    status = (
        _badge("失败", "danger") if _turn_failed(turn) else _badge("完成", "success")
    )
    if turn.get("interruption"):
        status += _badge("已打断", "warning")
    elif turn.get("requested_interruption"):
        status += _badge("打断未触发", "warning")
    if turn.get("input_settings", {}).get("mode") == "vad":
        status += _badge("VAD")
    return status


def _timing_values(turn):
    timing = turn["reply_timing"]
    rows = timing.get("sentences", [])
    heard = next((r for r in rows if r.get("start_seconds") is not None), {})
    answer = next(
        (
            r
            for r in rows
            if r.get("kind") == "answer" and r.get("start_seconds") is not None
        ),
        {},
    )
    temporary_ends = [
        r["end_seconds"]
        for r in rows
        if r.get("kind") in {"pre_speech", "filler"}
        and r.get("end_seconds") is not None
        and answer
        and r["end_seconds"] <= answer["start_seconds"]
    ]
    metrics = turn.get("metrics", {})

    def fallback(row, metric):
        value = row.get("start_seconds")
        return (
            value
            if value is not None
            else (metrics[metric] / 1000 if metrics.get(metric) is not None else None)
        )

    return [
        fallback(heard, "first_playback_ms"),
        fallback(answer, "first_answer_playback_ms"),
        answer["start_seconds"] - max(temporary_ends) if temporary_ends else None,
        timing.get("max_gap_seconds"),
    ]


def _cards(cards):
    return (
        '<div class="metric-grid">'
        + "".join(
            f'<div class="metric-card"><span>{_text(label)}</span><strong>{_text(value)}'
            f"<small>{_text(unit)}</small></strong><p>{_text(hint)}</p></div>"
            for label, value, unit, hint in cards
        )
        + "</div>"
    )


def _navigation(report, active):
    home = ' aria-current="page"' if active is None else ""
    links = [f'<a class="overview-link" href="report.html"{home}>会话总览</a>']
    links.append('<div class="nav-caption">本次对话</div>')
    for index, turn in enumerate(report["turns"]):
        current = ' aria-current="page"' if index == active else ""
        failed = " danger-dot" if _turn_failed(turn) else ""
        links.append(
            f'<a class="turn-link" href="turn-{index + 1:03}.html"{current}>'
            f'<span class="turn-number">{index + 1:02}</span><span class="turn-label">'
            f'{_text(_question(turn))}</span><span class="status-dot{failed}" aria-label="'
            f'{"失败" if _turn_failed(turn) else "完成"}"></span></a>'
        )
    return "".join(links)


def _notices(report, turn=None):
    messages = [report[key] for key in ("error", "close_error") if report.get(key)]
    if report.get("diagnostics") and not report["diagnostics"].get("complete"):
        messages.append("诊断数据不完整，部分阶段的时间可能缺失。")
    messages.extend(report.get("metric_limitations", []))
    if turn is not None:
        if turn.get("error"):
            messages.append(turn["error"])
        messages.extend(
            f'{check["name"]}：期望 {check["expected"]}，实际 {check["actual"]}'
            for check in turn.get("checks", [])
            if not check["passed"]
        )
    else:
        messages.extend(report.get("failures", []))
    return "".join(
        f'<div class="notice" role="status">{_text(message)}</div>'
        for message in dict.fromkeys(messages)
    )


def _overview(report):
    turns = report["turns"]
    values = [_timing_values(turn) for turn in turns]
    maximum = lambda column: max(
        (v[column] for v in values if v[column] is not None), default=None
    )
    completed = sum(not _turn_failed(turn) for turn in turns)
    content = (
        '<div class="page-heading"><div><div class="eyebrow">SESSION OVERVIEW</div>'
        f'<h1>{_text(report.get("name", "语音测试"))}</h1><p>一段连续对话，逐轮查看响应与内部时序。</p></div>'
        f'{_badge("测试通过", "success") if report.get("status") == "passed" else _badge("需要检查", "danger")}</div>'
        + _notices(report)
        + _cards(
            [
                (
                    "对话轮次",
                    f"{len(turns):02}",
                    "轮",
                    f"{completed} 轮完成 · {len(turns) - completed} 轮失败",
                ),
                ("最慢首句声音", _seconds(maximum(0)), "s", "从输入结束开始计时"),
                ("最慢正式回复", _seconds(maximum(1)), "s", "从输入结束开始计时"),
                ("最长过渡等待", _seconds(maximum(2)), "s", "临时结束 → 正式开始"),
            ]
        )
        + '<section class="panel"><div class="section-heading"><div><h2>对话记录</h2>'
        '<p>选择一轮，查看回复内容、回听和时间轴。</p></div><span class="unit-label">时间单位 · 秒</span></div>'
        '<div class="table-scroll"><table class="turn-table"><thead><tr>'
        "<th>轮次 / 用户输入</th><th>首句声音</th><th>正式回复</th><th>过渡等待</th><th>结果</th><th></th>"
        "</tr></thead><tbody>"
    )
    for index, (turn, metrics) in enumerate(zip(turns, values), 1):
        content += (
            '<tr><td><a class="question-link" href="'
            f'turn-{index:03}.html"><span class="table-number">{index:02}</span>'
            f"<span>{_text(_question(turn))}</span></a></td>"
            + "".join(
                f'<td class="numeric">{_seconds(v)}<small> s</small></td>'
                for v in metrics[:3]
            )
            + f'<td><div class="badges">{_turn_badges(turn)}</div></td>'
            f'<td><a class="open-turn" href="turn-{index:03}.html" aria-label="查看第 {index} 轮">查看 <span aria-hidden="true">→</span></a></td></tr>'
        )
    content += "</tbody></table></div></section>"
    content += (
        '<div class="overview-footer"><span>同一 WebSocket 会话内顺序执行，保留上下文。</span>'
        '<a href="report.json" download>下载报告数据</a></div>'
        '<details class="disclosure"><summary>运行信息与采集口径</summary><div class="detail-body">'
        "<p>播放时间来自 Python 模拟播放器；客户端与 VAS 各自使用单调时钟。"
        "“—”表示该项未采集或本轮不适用。</p>"
        f'<pre>{_text(json.dumps({"session": report.get("session"), "run": report.get("run_metadata"), "diagnostics": report.get("diagnostics")}, ensure_ascii=False, indent=2))}</pre>'
        "</div></details>"
    )
    return content


def _turn_page(report, index):
    turn = report["turns"][index]
    values = _timing_values(turn)
    metrics = turn.get("metrics", {})
    vad = turn.get("input_settings", {}).get("mode") == "vad"
    previous = (
        f'<a class="button" href="turn-{index:03}.html">← 上一轮</a>' if index else ""
    )
    following = (
        f'<a class="button" href="turn-{index + 2:03}.html">下一轮 →</a>'
        if index + 1 < len(report["turns"])
        else ""
    )
    content = (
        '<div class="page-heading"><div><div class="eyebrow">'
        f'TURN {index + 1:02} <span>/ {len(report["turns"]):02}</span></div>'
        f'<h1>第 {index + 1} 轮对话</h1><div class="badges">{_turn_badges(turn)}</div></div>'
        f'<div class="page-switch">{previous}{following}</div></div>'
        + _notices(report, turn)
        + '<section class="question-panel"><div class="question-kicker">用户输入</div>'
        f"<p>{_text(_question(turn))}</p>"
    )
    asr = metrics.get("asr_text")
    if asr and turn.get("input_text") and asr != turn["input_text"]:
        content += f'<div class="asr-text"><span>ASR 识别</span>{_text(asr)}</div>'
    content += "</section>"
    content += _cards(
        [
            ("首句声音", _seconds(values[0]), "s", "输入结束 → 开始播放"),
            ("首句正式回复", _seconds(values[1]), "s", "输入结束 → 正式开始"),
            ("过渡后的等待", _seconds(values[2]), "s", "临时结束 → 正式开始"),
            ("最长句间空档", _seconds(values[3]), "s", "上一段播完 → 下一段开始"),
        ]
    )
    content += '<section class="panel" id="reply-timing"></section>'
    if turn.get("interruption") or turn.get("requested_interruption"):
        interruption = turn.get("interruption") or {}
        requested = turn.get("requested_interruption") or {}
        lateness = interruption.get("trigger_lateness_ms")
        content += (
            '<section class="panel interruption-panel"><div class="section-heading"><h2>打断结果</h2>'
            + _badge("已发送打断" if interruption else "打断未触发", "warning")
            + '</div><div class="fact-grid">'
            f'<div><span>计划打断时间</span><strong>播放后 {_seconds(requested.get("after_playback_seconds"))} s</strong></div>'
            f"<div><span>触发误差</span><strong>{_seconds(lateness / 1000 if lateness is not None else None)} s</strong></div>"
            f'<div><span>VAS 收到取消</span><strong>{"已收到" if interruption.get("vas_abort_received") else "未观察到"}</strong></div>'
            f'<div><span>LLM 停止信号</span><strong>{"已观察到" if interruption.get("llm_abort_observed") else "未观察到"}</strong></div>'
            f'<div><span>TTS 停止信号</span><strong>{"已观察到" if interruption.get("tts_abort_observed") else "未观察到"}</strong></div>'
            "</div></section>"
        )
    content += (
        '<section class="panel"><div class="section-heading"><div><h2>链路时序</h2>'
        '<p id="clock-label">VAS 单调时钟 · 本轮首个已采集阶段 = 0 s</p></div>'
        '<div class="segmented-control" role="group" aria-label="时间轴来源">'
        '<button type="button" data-clock="server" aria-pressed="true">VAS 内部</button>'
        '<button type="button" data-clock="client" aria-pressed="false">客户端</button></div></div>'
        '<div class="request-counts">'
    )
    for label, key in (
        ("Memory", "memory_requests"),
        ("LLM", "llm_requests"),
        ("TTS", "tts_requests"),
        ("知识库", "knowledge_base_calls"),
    ):
        value = (
            "未采集"
            if report.get("run_metadata", {}).get("diagnostics") == "off"
            else metrics.get(key)
        )
        content += f"<span>{label}<b>{_text(value)}</b></span>"
    content += (
        '</div><div class="timeline-scroll"><div id="server" class="timeline"></div>'
        '<div id="client" class="timeline" hidden></div></div>'
        '<details class="inline-disclosure"><summary>请求明细与计时口径</summary><div class="detail-body">'
        "<p>两种时间轴分别归零，不能跨轴相减。TTS 按所属 LLM 分组；重叠请求的耗时不相加。"
        "ASR 全程包含音频上传。临时回复、过渡语与正式回复以已采集的类型为准。</p>"
        '<div class="table-scroll"><table id="spans"></table></div></div></details></section>'
        '<section class="panel compact-panel"><div class="section-heading"><h2>'
        + ("VAD 结束与识别收尾" if vad else "语音结束与识别收尾")
        + "</h2>"
        + _badge("VAD 自动结束" if vad else "手动停止")
        + '</div><div class="fact-grid">'
    )
    handoff_metrics = (
        [
            ("本地 VAD 静音判定", "local_vad_silence_ms"),
            ("ASR VAD → 最终文本", "asr_endpoint_to_final_ms"),
        ]
        if vad
        else [
            ("停止消息排队", "stop_queue_ms"),
            ("停止 → 开始处理", "stop_to_execution_ms"),
            ("ASR 提交 → 最终文本", "asr_commit_to_final_ms"),
        ]
    )
    for label, key in handoff_metrics:
        value = metrics.get(key)
        content += f"<div><span>{label}</span><strong>{_seconds(value / 1000 if value is not None else None)} s</strong></div>"
    content += '</div><details class="inline-disclosure"><summary>查看结束事件</summary><div class="table-scroll detail-body"><table id="handoff"></table></div></details></section>'
    checks = turn.get("checks", [])
    content += f'<details class="disclosure"><summary>回归断言 <span>{sum(c["passed"] for c in checks)} / {len(checks)} 通过</span></summary><div class="detail-body table-scroll"><table id="checks"></table></div></details>'
    content += '<details class="disclosure"><summary>本轮完整音频</summary><div class="detail-body full-audio">'
    for kind, name in turn.get("audio", {}).items():
        label = {
            "input": "用户输入",
            "received": "收到的回复",
            "played": "实际播放",
        }.get(kind, kind)
        content += f'<div><span>{_text(label)}</span><audio controls preload="none" aria-label="{_text(label)}" src="{quote(Path(name).name)}"></audio></div>'
    content += "</div></details>"
    content += '<details class="disclosure"><summary>原始诊断数据</summary><div class="detail-body"><a href="report.json" download>下载 JSON</a><pre id="raw"></pre></div></details>'
    content += f'<nav class="bottom-navigation" aria-label="轮次翻页"><a href="report.html">返回会话总览</a><div>{previous}{following}</div></nav>'
    return content


def build_report(report, path):
    """Write a session overview and independent, fully offline turn pages."""
    assets = Path(__file__).parent
    template = (assets / "report_template.html").read_text()
    style = (assets / "report.css").read_text() + (
        assets / "reply_timing.css"
    ).read_text()
    script = (assets / "reply_timing.js").read_text() + (
        assets / "report.js"
    ).read_text()
    display = copy.deepcopy(report)
    for turn in display["turns"]:
        if "reply_timing" not in turn:
            turn["reply_timing"] = analyze_reply_timing(turn)
        turn["timeline_lanes"] = group_timeline_spans(
            turn.get("spans", []), turn.get("vas_events", [])
        )
    environment = display.get("run_metadata", {}).get("environment", "本地报告")

    def page(index):
        turn = display["turns"][index] if index is not None else None
        encoded = (
            json.dumps({"turn": turn}, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace("\u2028", "\\u2028")
        )
        replacements = {
            "__TITLE__": _text(
                f'{display.get("name", "语音测试")} · '
                + (f"第 {index + 1} 轮" if turn is not None else "会话总览")
            ),
            "__PAGE__": "turn" if turn is not None else "overview",
            "__ENVIRONMENT__": _text(str(environment).upper()),
            "__STYLE__": style,
            "__NAVIGATION__": _navigation(display, index),
            "__CONTENT__": (
                _turn_page(display, index) if turn is not None else _overview(display)
            ),
            "__REPORT_DATA__": encoded,
            "__SCRIPT__": script if turn is not None else "",
        }
        return re.sub(
            r"__[A-Z_]+__", lambda match: replacements.get(match[0], match[0]), template
        )

    path = Path(path)
    overview = page(None)
    path.write_text(overview)
    (path.parent / "index.html").write_text(overview)
    for index in range(len(display["turns"])):
        (path.parent / f"turn-{index + 1:03}.html").write_text(page(index))
