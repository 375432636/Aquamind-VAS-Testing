"""Offline timing analysis and regression verdicts from raw, separate clock domains."""

import copy
import json
import re
from collections import Counter
from html import escape
from pathlib import Path

from .assertions import evaluate_business_assertions
from .clock_timeline import combined_timeline, session_trace_timeline
from .connection import connection_details
from .evaluation import TOOL_NAMES, tool_check
from .excel_report import export_excel
from .input_control import inspect_input_control, refresh_ptt_markers
from .key_moments import add_key_moment_lanes
from .llm_evidence import summarize_llm_requests
from .reply_timing import (
    analyze_reply_timing,
    first_playback_frame,
    input_end_event,
    ordered_playback_frames,
    packet_kind,
    playback_clock,
    validated_playback_frames,
)
from .timeline import group_timeline_spans, media_timeline_markers, request_spans
from .tts_evidence import tts_segment_evidence
from .turn_attribution import attribute_mixed_turns, attribute_reused_listen_turns


def delta(events, start, end, key, *, signed=False):
    a = next((e[key] for e in events if e["event"] == start), None)
    b = next((e[key] for e in events if e["event"] == end), None)
    return (
        (b - a) / 1e6
        if a is not None and b is not None and (signed or b >= a)
        else None
    )


def evaluate(result, vas_events, *, artifact_dir=None):
    report = copy.deepcopy(result)
    vas_events, failures = attribute_mixed_turns(report, vas_events)
    vas_events, reused_failures = attribute_reused_listen_turns(report, vas_events)
    failures.extend(reused_failures)
    attribution_complete = not failures
    failure_groups = {
        key: []
        for key in (
            "functional",
            "diagnostic_missing",
            "latency",
            "configuration_unverified",
        )
    }
    failure_groups["diagnostic_missing"].extend(failures)
    attributed = any("client_turn_index" in event for event in vas_events)
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
        failure_groups["diagnostic_missing"].append("诊断数据不完整")
    for index, turn in enumerate(report["turns"], 1):
        rows = [
            e
            for e in vas_events
            if (
                e.get("client_turn_index") == index
                if attributed
                else e.get("listen_turn_id")
                in turn.get(
                    "server_listen_turn_ids",
                    [
                        turn.get(
                            "server_listen_turn_id", turn.get("listen_turn_id", index)
                        )
                    ],
                )
            )
        ]
        turn["vas_events"] = rows
        turn["input_control"] = inspect_input_control(turn)
        turn["llm_requests"] = summarize_llm_requests(rows)
        turn["asr_settings"] = [
            e
            for e in rows
            if e["event"]
            in {"asr_session_config_requested", "asr_session_config_confirmed"}
        ]
        spans, span_errors = request_spans(rows)
        # Server diagnostics may annotate a verified sentence exchange, but never
        # rewrite client packets: their audio counters are independent.
        # A chunked PTT turn has several server listen IDs but one client ID.
        # Canonicalize only this annotation view using the recorded mapping;
        # preserve every original server event and clock in the report.
        annotation_rows = rows
        if (
            turn.get("server_listen_turn_ids")
            or turn.get("server_listen_turn_id") is not None
        ):
            annotation_rows = [
                {**row, "listen_turn_id": turn.get("listen_turn_id", index)}
                for row in rows
            ]
        texts = tts_segment_evidence(spans, annotation_rows, turn["events"])
        for span in spans:
            if span["span_id"] in texts:
                span["tts_evidence"] = texts[span["span_id"]]
        from .tts_evidence import cached_audio_annotations

        turn["reply_annotations"] = cached_audio_annotations(
            annotation_rows, turn["events"]
        )
        for span in spans:
            evidence = texts.get(span["span_id"], {})
            if evidence.get("status") == "matched":
                for seq in evidence.get("audio_seqs", []):
                    turn["reply_annotations"][str(seq)] = {
                        "output_kind": evidence.get("output_kind"),
                        "output_id": evidence.get("output_id"),
                        "source": evidence["source"],
                        "span_id": span["span_id"],
                    }
        failures.extend(span_errors)
        failure_groups["diagnostic_missing"].extend(span_errors)
        turn["spans"] = spans
        counts = Counter(e["event"] for e in rows)
        tools = Counter(
            e["data"]["tool_name"] for e in rows if e["event"] == "tool_call_started"
        )
        media_counts = Counter(
            marker["kind"] for marker in media_timeline_markers(turn["events"])
        )
        turn["playback_clock"] = playback_clock(turn)
        played_frames = validated_playback_frames(turn)
        playback_events = turn["events"]
        if turn["playback_clock"]["status"] in {"invalid", "estimated"}:
            playback_events = []
            report["metric_limitations"].append(
                f"{turn['id']}：浏览器播放时钟无效或仅有估计值，首音和播放分段时间不可用；首包等待仍使用原始客户端记录。"
            )
        elif turn.get("playback_source") == "browser_audio_context":
            playback_events = [
                e for e in turn["events"] if e["event"] != "playback_started"
            ]
            first_frame = first_playback_frame(turn)
            if first_frame is not None:
                playback_events.append(
                    {"event": "playback_started", "at_ns": first_frame["at_ns"]}
                )
        metrics = {
            "image_items": media_counts["image"],
            "video_items": media_counts["video"],
            "asr_text": next(
                (
                    e.get("data", {}).get("text")
                    for e in reversed(turn["events"])
                    if e["event"] == "stt"
                ),
                None,
            ),
            "first_playback_ms": delta(
                playback_events,
                input_end_event(turn),
                "playback_started",
                "at_ns",
                signed=True,
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
            "input_finish_to_asr_endpoint_ms": None,
            "input_finish_to_asr_endpoint_observed_ms": _endpoint_arrival_delay(
                turn, rows
            ),
            "local_vad_starts": counts["local_vad_speech_started"],
            "local_vad_ends": counts["local_vad_endpoint_detected"],
            "asr_vad_starts": counts["asr_speech_started"],
            "asr_vad_ends": counts["asr_endpoint_detected"],
            "speech_utterances": counts["speech_chunk_started"],
            "speech_chunks": counts["speech_chunk_started"]
            + counts["speech_continuation_detected"],
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
                or (
                    e["data"].get("category") in {None, "other"}
                    and e["data"].get("tool_name") in TOOL_NAMES["知识库-文字"]
                )
                for e in rows
                if e["event"] == "tool_call_started"
            ),
        }
        if turn.get("sensor"):
            turn["server_input_text"] = metrics["asr_text"]
            metrics["asr_text"] = None
        packets = {
            e["data"].get("audio_seq"): e["data"]
            for e in turn["events"]
            if e["event"] == "audio_received"
        }
        first_answer = next(
            (
                e
                for e in ordered_playback_frames(turn)
                if packet_kind(
                    turn,
                    e["data"].get("audio_seq"),
                    packets.get(e["data"].get("audio_seq"), {}),
                )
                == "answer"
            ),
            None,
        )
        answer_ns = (
            first_answer["at_ns"]
            if first_answer is not None and first_answer in played_frames
            else None
        )
        stop_ns = next(
            (e["at_ns"] for e in turn["events"] if e["event"] == input_end_event(turn)),
            None,
        )
        metrics["first_answer_playback_ms"] = (
            (answer_ns - stop_ns) / 1e6
            if answer_ns is not None and stop_ns is not None and answer_ns >= stop_ns
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
            if name == "business":
                continue
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
            kind = (
                "diagnostic_missing"
                if actual is None
                else "latency" if name.startswith("max_") else "functional"
            )
            reason = "" if passed else f"{name} 期望 {expected}，实际 {actual}"
            checks.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": actual,
                    "passed": passed,
                    "status": (
                        "passed"
                        if passed
                        else "unknown" if actual is None else "failed"
                    ),
                    "failure_kind": None if passed else kind,
                    "reason": reason,
                }
            )
            if not passed:
                failure = f"{turn['id']}: {reason}"
                failures.append(failure)
                failure_groups[kind].append(failure)
        business_checks = evaluate_business_assertions(
            turn,
            rows,
            diagnostics_complete=(
                report.get("diagnostics", {}).get("complete")
                if attribution_complete
                else False
            ),
            artifact_dir=artifact_dir,
        )
        if turn.get("tool"):
            turn["tool_check"] = tool_check(
                turn["tool"],
                rows,
                (
                    report.get("diagnostics", {}).get("complete")
                    if attribution_complete
                    else False
                ),
            )
            business_checks.append(turn["tool_check"])
        checks.extend(business_checks)
        for check in business_checks:
            if not check["passed"]:
                failure = f"{turn['id']}: {check['name']} {check['reason']}"
                failures.append(failure)
                failure_groups[check.get("failure_kind") or "functional"].append(
                    failure
                )
        turn["checks"] = checks
        if turn["status"] == "failed":
            failures.append(f"{turn['id']}: {turn.get('error','运行失败')}")
            failure_groups["functional"].append(failures[-1])
    report["session_events"] = [
        e
        for e in vas_events
        if (
            not e.get("client_turn_index")
            if attributed
            else e.get("listen_turn_id") is None
        )
    ]
    report["failures"] = failures
    report["failure_groups"] = failure_groups
    if failures:
        report["status"] = "failed"
    return report


def _endpoint_arrival_delay(turn, rows):
    """Client-clock observation only; includes diagnostic delivery latency."""
    zero = next(
        (
            e["at_ns"]
            for e in turn.get("events", [])
            if e["event"] == "speech_input_finished"
        ),
        None,
    )
    endpoints = {
        (e.get("clock_id"), e.get("monotonic_ns"), e.get("span_id"))
        for e in rows
        if e["event"] == "asr_endpoint_detected"
    }
    received = next(
        (
            e["at_ns"]
            for e in turn.get("events", [])
            if e["event"] == "vas_event"
            and e.get("data", {}).get("event") == "asr_endpoint_detected"
            and (
                e["data"].get("clock_id"),
                e["data"].get("monotonic_ns"),
                e["data"].get("span_id"),
            )
            in endpoints
        ),
        None,
    )
    return (
        (received - zero) / 1e6 if zero is not None and received is not None else None
    )


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
        _badge("失败", "danger")
        if _turn_failed(turn)
        else (
            _badge("已打断", "warning")
            if turn.get("status") == "interrupted"
            else _badge("完成", "success")
        )
    )
    if turn.get("interruption") and turn.get("status") != "interrupted":
        status += _badge("已打断", "warning")
    elif not turn.get("interruption") and turn.get("requested_interruption"):
        status += _badge("打断未触发", "warning")
    if turn.get("input_settings", {}).get("mode") == "vad":
        status += _badge("VAD")
    if turn.get("sensor"):
        status += _badge("传感器")
    return status


def _timing_values(turn):
    timing = turn["reply_timing"]
    rows = [r for r in timing.get("sentences", []) if r.get("kind") != "greeting"]
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
        and r.get("end_confirmed", r.get("status") == "completed")
        and r.get("end_seconds") is not None
        and answer
        and r["end_seconds"] <= answer["start_seconds"]
    ]
    metrics = turn.get("metrics", {})

    def fallback(row, metric):
        if metric in metrics:
            return metrics[metric] / 1000 if metrics[metric] is not None else None
        return row.get("start_seconds")

    return [
        (
            metrics["first_playback_ms"] / 1000
            if metrics.get("first_playback_ms") is not None
            else None
        ),
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
        state = (
            "失败"
            if _turn_failed(turn)
            else "已打断" if turn.get("status") == "interrupted" else "完成"
        )
        failed = (
            " danger-dot"
            if state == "失败"
            else " warning-dot" if state == "已打断" else ""
        )
        links.append(
            f'<a class="turn-link" href="turn-{index + 1:03}.html"{current}>'
            f'<span class="turn-number">{index + 1:02}</span><span class="turn-label">'
            f'{_text(_question(turn))}</span><span class="status-dot{failed}" aria-label="'
            f'{state}"></span></a>'
        )
    return "".join(links)


def _notices(report, turn=None):
    messages = [report[key] for key in ("error", "close_error") if report.get(key)]
    unreliable = [
        str(index)
        for index, item in enumerate(report.get("turns", []), 1)
        if (turn is None or item is turn)
        and playback_clock(item)["status"] in {"invalid", "estimated"}
    ]
    if unreliable:
        messages.insert(
            0,
            f"第 {'、'.join(unreliable)} 轮缺少可靠播放时钟，无法还原真实首音。首包等待不等于首音等待；整段回听未包含无法对齐的回复，可在单轮页面回听原始回复音频。",
        )
    partial = [
        str(index)
        for index, item in enumerate(report.get("turns", []), 1)
        if (turn is None or item is turn)
        and playback_clock(item)["status"] == "partial"
    ]
    if partial:
        messages.insert(
            0,
            f"第 {'、'.join(partial)} 轮存在少量播放计时不确定区间；其余已确认区间保留在时间轴和整段回听，完整原始回复可在单轮页面回听。",
        )
    if report.get("data_source") == "mock":
        messages.insert(
            0,
            "MOCK 模拟数据 · 未连接 VAS，回复及时间仅用于验证测试工具，不代表真实服务表现。",
        )
    if report.get("diagnostics") and not report["diagnostics"].get("complete"):
        messages.append(
            "诊断数据不完整：VAS 缺少结束数据，部分服务端环节可能缺失；客户端录音和播放记录独立保留。"
        )
    # Clock failures are summarized once above; keep per-turn detail in JSON.
    messages.extend(
        message
        for message in report.get("metric_limitations", [])
        if "浏览器播放时钟无效或仅有估计值" not in message
    )
    if turn is not None:
        if turn.get("error"):
            messages.append(turn["error"])
        messages.extend(
            (
                f'{check["name"]}：{check["reason"]}'
                if check.get("reason")
                else f'{check["name"]}：期望 {check["expected"]}，实际 {check["actual"]}'
            )
            for check in turn.get("checks", [])
            if not check["passed"]
        )
    else:
        messages.extend(m for m in report.get("failures", []) if m != "诊断数据不完整")
    labels = {
        "functional": "功能失败",
        "diagnostic_missing": "诊断缺失",
        "latency": "延迟超标",
        "configuration_unverified": "配置未核实",
    }
    groups = report.get("failure_groups", {})
    category_summary = " · ".join(
        f"{labels[key]} {len(items)}" for key, items in groups.items() if items
    )
    messages = list(dict.fromkeys(messages))
    summary = (
        f'<div class="notice" role="status">{_text(category_summary)}</div>'
        if category_summary
        else ""
    )
    if len(messages) > 2:
        return summary + (
            f'<details class="inline-disclosure"><summary>查看 {len(messages)} 项采集说明</summary>'
            '<div class="detail-body">'
            + "".join(f"<p>{_text(message)}</p>" for message in messages)
            + "</div></details>"
        )
    return summary + "".join(
        f'<div class="notice" role="status">{_text(message)}</div>'
        for message in messages
    )


def _input_control_panel(turns):
    rows = []
    for index, turn in enumerate(turns, 1):
        if turn.get("input_type") != "audio" and turn.get("input_settings", {}).get(
            "mode"
        ) not in {"manual", "vad"}:
            continue
        state = inspect_input_control(turn)
        mode = {"manual": "按住说话 · 松开结束", "vad": "VAD 自动结束"}.get(
            state["mode"], "未记录"
        )
        requested = {"off": "请求关闭", "on": "请求开启", "unknown": "未记录"}[
            state["requested_vad"]
        ]
        confirmed = {"off": "已确认关闭", "on": "已确认开启", "unknown": "未明确回显"}[
            state["confirmed_vad"]
        ]
        local = (
            f'仍在运行 · {state["local_vad_events"]} 个事件'
            if state["local_vad"] == "observed"
            else "未观察到，不能确认关闭"
        )
        finalization = {
            "manual_commit": "收到 stop → 提交 ASR → 最终结果",
            "early_final": "注意：收到 stop 前已有最终结果",
            "unknown": "未确认",
        }[state["finalization"]]
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{_text(value)}</td>"
                for value in (
                    turn.get("id", str(index)),
                    mode,
                    f'已发 {state["stop_sent"]} 次 / VAS 已收 {state["stop_received"]} 次',
                    requested + " · " + confirmed,
                    local,
                    finalization,
                )
            )
            + "</tr>"
        )
    if not rows:
        return ""
    return (
        '<section class="panel input-control-panel"><div class="section-heading"><h2>录音结束控制</h2></div>'
        '<div class="table-scroll"><table><thead><tr>'
        "<th>轮次</th><th>输入模式</th><th>结束信号 stop</th><th>ASR 自动断句</th><th>VAS 本地 VAD</th><th>实际结束顺序</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        '<p class="measurement-hint">手动模式按下发 start，松开发 stop。关闭 ASR 自动断句不等于停止 VAS 本地 VAD；未回显或未观察到均不视为已关闭。</p></section>'
    )


def _connection_strip(report):
    recorded = report.get("connection") or {}
    run = report.get("run_metadata") or {}
    details = connection_details(
        recorded.get("device_mac") or run.get("device_id"),
        recorded.get("websocket_url") or run.get("endpoint"),
    )
    if not details:
        return ""
    fields = []
    for label, key in (("设备 MAC", "device_mac"), ("测试 WebSocket", "websocket_url")):
        if details.get(key):
            fields.append(f"<span>{label} <code>{_text(details[key])}</code></span>")
    return (
        '<div class="connection-strip" aria-label="测试连接信息">'
        + "".join(fields)
        + "</div>"
    )


def _overview(report):
    turns = report["turns"]
    values = [_timing_values(turn) for turn in turns]
    maximum = lambda column: max(
        (v[column] for v in values if v[column] is not None), default=None
    )
    failed = sum(_turn_failed(turn) for turn in turns)
    interrupted = sum(
        turn.get("status") == "interrupted" and not _turn_failed(turn) for turn in turns
    )
    completed = len(turns) - failed - interrupted
    content = (
        '<div class="page-heading"><div><div class="eyebrow">SESSION OVERVIEW</div>'
        f'<h1>{_text(report.get("name", "语音测试"))}</h1><p>整段会话 · 用户语音、回复播放与 VAS 全链路时序。</p></div>'
        f'{_badge("测试通过", "success") if report.get("status") == "passed" else _badge("需要检查", "danger")}</div>'
        + _connection_strip(report)
        + _notices(report)
        + (
            '<p><a href="evaluation.xlsx" download>下载 Excel 评测表</a></p>'
            if report.get("evaluation")
            else ""
        )
        + _cards(
            [
                (
                    "对话轮次",
                    f"{len(turns):02}",
                    "轮",
                    f"{completed} 轮完成 · {interrupted} 轮打断 · {failed} 轮失败",
                ),
                ("最慢首句声音", _seconds(maximum(0)), "s", "从输入结束开始计时"),
                ("最慢正式回复", _seconds(maximum(1)), "s", "从输入结束开始计时"),
                ("最长过渡等待", _seconds(maximum(2)), "s", "临时结束 → 正式开始"),
            ]
        )
        + _session_panel(report)
        + _input_control_panel(turns)
        + '<section class="panel"><div class="section-heading"><div><h2>逐轮结果</h2>'
        '<p>上方查看全会话时序，详细页聚焦单轮。</p></div><span class="unit-label">时间单位 · 秒</span></div>'
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
            f'<td><div class="turn-actions"><a class="open-turn" data-session-seek="{_session_start(report, index):.3f}" href="#session-timeline" aria-label="回听第 {index} 轮">回听</a>'
            f'<a class="open-turn" href="turn-{index:03}.html" aria-label="查看第 {index} 轮">时序 →</a></div></td></tr>'
        )
    content += "</tbody></table></div></section>"
    content += (
        '<div class="overview-footer"><span>同一 WebSocket 会话内顺序执行，保留上下文。</span>'
        '<a href="report.json" download>下载报告数据</a></div>'
        '<details class="disclosure"><summary>运行信息与采集口径</summary><div class="detail-body">'
        "<p>自动测试使用 Python 模拟播放；网页录制使用浏览器音频时钟。客户端与 VAS 各自保留原始计时。"
        "“—”表示该项未采集或本轮不适用。</p>"
        f'<pre>{_text(json.dumps({"session": report.get("session"), "run": report.get("run_metadata"), "diagnostics": report.get("diagnostics")}, ensure_ascii=False, indent=2))}</pre>'
        "</div></details>"
    )
    return content


def _session_start(report, index):
    row = next(
        (
            row
            for row in report.get("session_playback", {}).get("turns", [])
            if row["index"] == index
        ),
        {},
    )
    return row.get("input_start_seconds") or row.get("start_seconds") or 0


def _turn_playback(playback, turn, index):
    """A single turn's view onto the original session clock and audio file."""
    view = {
        key: value
        for key, value in playback.items()
        if key
        not in {
            "turns",
            "segments",
            "waits",
            "markers",
            "media_markers",
            "vas_timeline",
        }
    }
    view["view_turn_index"] = index
    for key in ("turns", "segments", "waits", "markers", "media_markers"):
        field = "index" if key == "turns" else "turn_index"
        view[key] = [row for row in playback.get(key, []) if row.get(field) == index]
    trace = playback.get("vas_timeline", {})
    view["vas_timeline"] = dict(
        trace,
        lanes=[
            lane for lane in trace.get("lanes", []) if lane.get("turn_index") == index
        ],
    )
    bounds = []

    def collect(row):
        for key in (
            "start_seconds",
            "input_start_seconds",
            "input_end_seconds",
            "end_seconds",
            "at_seconds",
        ):
            if isinstance(row.get(key), (int, float)):
                bounds.append(row[key])
        for segment in row.get("input_segments", []):
            collect(segment)

    for key in ("turns", "segments", "waits", "markers", "media_markers"):
        for row in view[key]:
            collect(row)
    zero = playback.get("zero_at_ns")
    if zero is not None:
        for key in ("started_at_ns", "ended_at_ns"):
            if turn.get(key) is not None:
                bounds.append((turn[key] - zero) / 1e9)
    for lane in view["vas_timeline"]["lanes"]:
        for item in lane.get("segments", []) + lane.get("markers", []):
            for key in ("plot_start_ns", "plot_end_ns"):
                if isinstance(item.get(key), (int, float)):
                    bounds.append(item[key] / 1e9)
    start, end = (min(bounds), max(bounds)) if bounds else (0, 1)
    if end <= start:
        later = [
            row["start_seconds"]
            for row in playback.get("turns", [])
            if row.get("start_seconds", start) > start
        ]
        end = (
            min(later) if later else max(start + 1, playback.get("duration_seconds", 0))
        )
    padding = min(0.5, (end - start) * 0.025)
    view.update(
        view_start_seconds=start - padding if start > 0 else start,
        view_end_seconds=end + padding,
    )
    if view["turns"]:
        row = view["turns"][0]
        view["initial_time_seconds"] = row.get(
            "input_start_seconds", row.get("start_seconds", max(0, start))
        )
    return view


def _session_panel(report):
    playback = report.get("session_playback", {})
    trace = playback.get("vas_timeline", {})
    calibrated = (trace.get("clock_sync") or {}).get("status") == "calibrated"
    clock_label = (
        "北京时间 UTC+8 · 会话起点 = 0 s · "
        + ("VAS 已校准至客户端" if calibrated else "两端未校时")
        if trace.get("mode") == "wall"
        else (
            "相对时间 · 客户端与 VAS 各自从 0 s 开始，不能跨来源相减"
            if trace.get("lanes")
            else "客户端实时时间 · 开始发送音频 = 0 s"
        )
    )
    path = playback.get("playback_path") or playback.get("path")
    duration = playback.get("duration_seconds", 0)
    turn_index = playback.get("view_turn_index")
    title = f"第 {turn_index} 轮全链路时序" if turn_index else "会话全链路时序"
    content = (
        '<section class="panel session-panel" id="session-timeline"><div class="section-heading">'
        f'<div><div class="eyebrow">SESSION TIMELINE</div><h2>{title}</h2>'
        f'<p id="session-clock-label">{clock_label}</p></div>'
        f'<span class="unit-label">全程 {_seconds(duration)} s</span></div>'
    )
    if report.get("playback_reconstruction"):
        content += '<p class="session-capture-notice">模拟播放重建 · 按音频就绪时间恢复连续播放，原始录制保持不变。</p>'
    if path:
        content += (
            '<div class="session-player-row"><audio id="session-player" controls preload="metadata" '
            f'aria-label="完整会话播放" src="{_text(path)}"></audio>'
            f'<a class="session-download" href="{_text(path)}" download>下载会话 {_text(Path(path).suffix[1:].upper())} ↓</a></div>'
        )
    else:
        content += '<div class="empty-state">没有可回放的完整会话音频</div>'
    adjusted_input = any(
        isinstance(item, dict)
        and item.get("code") in {"approximate_input_timing", "input_packet_overlap"}
        for item in playback.get("limitations", [])
    )
    unreliable_reply = any(
        isinstance(item, dict)
        and item.get("code")
        in {
            "invalid_playback_clock",
            "estimated_playback_clock",
            "reply_audio_unavailable",
            "greeting_audio_unavailable",
        }
        for item in playback.get("limitations", [])
    )
    if unreliable_reply:
        content += '<p class="session-capture-notice">整段回听未包含无法对齐的回复；原始回复音频可在单轮页面单独回听。首包等待不等于首音等待。</p>'
    if adjusted_input:
        content += '<p class="session-capture-notice">输入回听为保留全部采样可能有微小顺延；时间轴显示原始发送时刻，历史缺帧时刻的输入另标为近似。</p>'
    content += (
        '<div class="session-toolbar"><div class="session-legend">'
        '<span><i class="legend-input"></i>用户输入</span><span><i class="legend-temporary"></i>过渡 / 临时</span>'
        '<span><i class="legend-answer"></i>正式回复</span><span><i class="legend-wait"></i>等待</span>'
        '<span><i class="legend-abort"></i>打断</span>'
        + (
            "".join(
                f'<span><i class="legend-{kind}"></i>{label}到达</span>'
                for kind, label in (("image", "图片"), ("video", "视频"))
                if any(m["kind"] == kind for m in playback.get("media_markers", []))
            )
        )
        + "</div>"
        '<div class="session-zoom" role="group" aria-label="时间轴缩放">'
        '<button type="button" data-session-zoom="out" aria-label="缩小时间轴">−</button>'
        f'<button type="button" data-session-zoom="fit">{"本轮" if turn_index else "全会话"}</button>'
        '<button type="button" data-session-zoom="in" aria-label="放大时间轴">+</button></div></div>'
        '<div class="session-tools"><span class="measurement-hint">点击查看 · 拖动测量 · 双指捏合缩放 · 双指平移滚动</span>'
        '<div class="measurement-controls" role="group" aria-label="时间区间测量">'
        '<label>起点 <input id="measure-start" type="number" min="0" step="0.01" placeholder="—" aria-label="测量起点，秒"> s</label>'
        '<span aria-hidden="true">→</span>'
        '<label>终点 <input id="measure-end" type="number" min="0" step="0.01" placeholder="—" aria-label="测量终点，秒"> s</label>'
        '<output id="measure-duration" aria-live="polite">Δ — s</output>'
        '<button type="button" id="measure-clear">清除</button></div></div>'
        '<div class="session-chart-layout"><div class="session-label-viewport"><div class="session-track-labels" id="session-track-labels">'
        '<span>用户语音</span><span>回复播放</span></div></div><div class="session-viewport" id="session-viewport">'
        '<div class="session-canvas" id="session-canvas" tabindex="0" role="group" '
        'aria-label="会话全链路时间轴，上方用户语音和回复，下方 VAS；拖动测量，也可输入起止秒数"></div></div></div>'
        '<div id="session-detail" class="session-detail" aria-live="polite">'
        '<span class="detail-placeholder">选择语音、回复或 VAS 节点，查看内容与时间。</span></div>'
        '<details class="inline-disclosure"><summary>播放与计时口径</summary><div class="detail-body">'
        "<p>按客户端记录的发送、播放时间还原整段会话，保留句间与轮间空档。"
        "自动测试记录来自 Python 模拟播放器；网页对话记录来自浏览器音频时钟，均不代表扬声器声学实测。</p>"
        + (
            "<p>本页仅展示当前轮，时间与完整会话一致，回听仍使用同一音频文件。"
            if turn_index
            else "<p>VAS 展示所有轮次及欢迎语等会话级阶段，维度与详细页一致。"
        )
        + "校准仅调整 VAS 在图中的位置，客户端语音、播放、等待和回听不受影响；校准状态和不确定范围见页首。旧记录缺少校准样本时保留原始时间。"
        "超出音频长度的 VAS 记录仍显示，音频回听长度不变。</p>"
        "<p>自动测试以 WAV 语音内容发送结束计时；网页 VAD 以客户端能量估计语音结束，单独标明，非服务端 VAD 事件。传感器以指令发出作为输入时刻，不生成输入音频。</p>"
    )
    if playback.get("playback_path"):
        content += "<p>默认回听混合用户与 VAS 的声音；分轨文件保留用户左声道、回复右声道的原始音频。</p>"
        if playback.get("path"):
            content += f'<p><a href="{_text(playback["path"])}" download>下载原始分轨 {_text(Path(playback["path"]).suffix[1:].upper())} ↓</a></p>'
    elif path:
        content += "<p>此历史报告使用分轨音频：用户在左声道，回复在右声道。重新生成报告可获得混音回听。</p>"
    for limitation in playback.get("limitations", []):
        message = (
            limitation.get("message", "")
            if isinstance(limitation, dict)
            else limitation
        )
        content += f'<p class="capture-limitation">{_text(message)}</p>'
    if playback.get("status") == "incomplete":
        content += '<p class="capture-limitation">部分音频或时间记录缺失，回放有未还原的区间。</p>'
    return content + "</div></details></section>"


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
        + _connection_strip(report)
        + _notices(report, turn)
        + '<section class="question-panel"><div class="question-kicker">用户输入</div>'
        f"<p>{_text(_question(turn))}</p>"
    )
    asr = metrics.get("asr_text")
    if asr and turn.get("input_text") and asr != turn["input_text"]:
        content += f'<div class="asr-text"><span>ASR 识别</span>{_text(asr)}</div>'
    if turn.get("sensor"):
        content += f'<div class="asr-text"><span>传感器指令</span>{_text(turn["sensor"])} · 不经过 ASR / VAD</div>'
        if turn.get("server_input_text"):
            content += f'<div class="asr-text"><span>服务端事件回显</span>{_text(turn["server_input_text"])}</div>'
    content += "</section>"
    playback_row = next(
        (
            row
            for row in report.get("session_playback", {}).get("turns", [])
            if row["index"] == index + 1
        ),
        {},
    )
    unaligned = playback_row.get("unaligned_audio", {})
    if not unaligned and playback_clock(turn)["status"] in {
        "invalid",
        "estimated",
        "partial",
    }:
        unaligned = turn.get("audio", {})
    raw_reply = unaligned.get("played") or unaligned.get("received")
    if raw_reply:
        source_label = (
            "已播放音频采样"
            if unaligned.get("played")
            else "已接收音频采样（不代表已播放）"
        )
        content += (
            '<section class="panel"><h2>原始回复回听（未对齐）</h2>'
            f"<p>{source_label}按原始文件顺序回听；缺少可靠播放时刻，不能据此测量首音等待或与整段时间轴对齐。</p>"
            f'<audio controls preload="metadata" aria-label="原始回复回听，未对齐" src="{_text(raw_reply)}"></audio>'
            f'<p><a href="{_text(raw_reply)}" download>下载原始回复 WAV</a></p></section>'
        )
    content += _cards(
        [
            ("首句声音", _seconds(values[0]), "s", "输入结束 → 开始播放"),
            ("首句正式回复", _seconds(values[1]), "s", "输入结束 → 正式开始"),
            ("过渡后的等待", _seconds(values[2]), "s", "临时结束 → 正式开始"),
            ("最长句间空档", _seconds(values[3]), "s", "上一段播完 → 下一段开始"),
        ]
    )
    content += (
        '<section class="panel" id="reply-timing"></section>'
        f'<a class="button session-return" href="report.html?t={_session_start(report, index + 1):.3f}#session-timeline">'
        "在完整会话中回听本轮 →</a>"
    )
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
    content += _session_panel(
        dict(
            report,
            session_playback=_turn_playback(
                report.get("session_playback", {}), turn, index + 1
            ),
        )
    )
    content += _input_control_panel([turn])
    content += (
        '<section class="panel"><div class="section-heading"><div><h2>请求明细</h2></div>'
        '<span class="badge">客户端 + VAS · 未校时</span></div>'
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
        if value is None:
            value = "未采集"
        content += f"<span>{label}<b>{_text(value)}</b></span>"
    content += (
        "</div>"
        '<details class="inline-disclosure"><summary>请求明细与计时口径</summary><div class="detail-body">'
        "<p>绝对时刻统一显示北京时间（UTC+8）；客户端以会话开始时的本机系统时间换算。"
        "两端未做时钟偏移校准，跨端间隔可能包含时钟误差。耗时统计仍使用各端单调时钟。"
        "旧记录缺少绝对时间时，各来源独立归零，不能跨来源相减。"
        "TTS 按所属 LLM 分组；重叠请求的耗时不相加。"
        "ASR 全程包含音频上传。临时回复、过渡语与正式回复以已采集的类型为准。</p>"
        '<div class="table-scroll"><table id="spans"></table></div></div></details></section>'
        '<section class="panel compact-panel"><div class="section-heading"><h2>'
        + (
            "传感器触发"
            if turn.get("sensor")
            else "VAD 结束与识别收尾" if vad else "语音结束与识别收尾"
        )
        + "</h2>"
        + _badge(
            "sensor 指令"
            if turn.get("sensor")
            else "VAD 自动结束" if vad else "手动停止"
        )
        + '</div><div class="fact-grid">'
    )
    handoff_metrics = (
        [("指令发送 → 首次播放", "first_playback_ms")]
        if turn.get("sensor")
        else (
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
    )
    for label, key in handoff_metrics:
        value = metrics.get(key)
        content += f"<div><span>{label}</span><strong>{_seconds(value / 1000 if value is not None else None)} s</strong></div>"
    content += "</div>"
    if vad:
        requested = [
            e
            for e in turn.get("asr_settings", [])
            if e["event"] == "asr_session_config_requested"
        ]
        confirmed = [
            e
            for e in turn.get("asr_settings", [])
            if e["event"] == "asr_session_config_confirmed"
        ]
        request_value = (
            requested[-1].get("data", {}).get("requested_silence_duration_ms")
            if requested
            else None
        )
        confirm_value = (
            confirmed[-1].get("data", {}).get("confirmed_silence_duration_ms")
            if confirmed
            else None
        )
        content += (
            '<p class="muted">ASR 静音阈值：请求 '
            + (
                f"{escape(str(request_value))} ms"
                if request_value is not None
                else "未知"
            )
            + " · 服务端确认 "
            + (
                f"{escape(str(confirm_value))} ms"
                if confirm_value is not None
                else "未知"
            )
            + "</p>"
        )
        observed = metrics.get("input_finish_to_asr_endpoint_observed_ms")
        content += (
            '<p class="muted">说完 → ASR 判停：精确间隔未知（客户端与服务端时钟独立）。客户端收到判停事件：'
            + (f"{observed / 1000:.3f} s" if observed is not None else "未知")
            + "，含诊断推送与网络延迟；负值表示事件在音频素材发送结束前到达，需检查提前断句。</p>"
        )
    content += '<details class="inline-disclosure"><summary>查看结束事件</summary><div class="table-scroll detail-body"><table id="handoff"></table></div></details></section>'
    content += _llm_evidence_panel(turn)
    checks = turn.get("checks", [])
    content += f'<details class="disclosure"><summary>回归断言 <span>{sum(c["passed"] for c in checks)} / {len(checks)} 通过</span></summary><div class="detail-body table-scroll"><table id="checks"></table></div></details>'
    content += '<details class="disclosure"><summary>原始诊断数据</summary><div class="detail-body"><a href="report.json" download>下载 JSON</a><pre id="raw"></pre></div></details>'
    content += f'<nav class="bottom-navigation" aria-label="轮次翻页"><a href="report.html">返回会话总览</a><div>{previous}{following}</div></nav>'
    return content


def _llm_evidence_panel(turn):
    rows = turn.get("llm_requests", [])
    if not rows:
        return ""

    def value(item):
        return escape(str(item)) if item is not None else "未知"

    def duration(item):
        return f"{item / 1000:.3f} s" if item is not None else "未知"

    headings = [
        "请求 / 尝试 / SDK 重试 / 传输重试",
        "供应商 · 模型",
        "连接",
        "输入 / 缓存 / 输出 Token",
        "TCP / TLS",
        "发送完成 → 响应头",
        "响应头 → 首块",
        "首块 → 有效输出",
        "状态",
        "流水线选择",
    ]
    body = []
    for row in rows:
        cells = [
            " / ".join(
                value(row.get(k))
                for k in (
                    "llm_request_seq",
                    "http_request_seq",
                    "retry_count",
                    "transport_retry_count",
                )
            ),
            " · ".join(value(row.get(k)) for k in ("provider", "model"))
            + " · "
            + value(row.get("thinking_mode")),
            {"new": "新建", "reused": "复用"}.get(row.get("connection_state"), "未知"),
            " / ".join(
                value(row.get(k))
                for k in ("input_tokens", "cached_tokens", "output_tokens")
            ),
            " / ".join(duration(row.get(k)) for k in ("tcp_ms", "tls_ms")),
            duration(row.get("sent_to_headers_ms")),
            duration(row.get("headers_to_first_sse_ms")),
            duration(row.get("first_sse_to_output_ms")),
            (
                "HTTP 未采集"
                if row.get("evidence_status") == "unknown"
                else value(row.get("status"))
            ),
        ]
        cells.append(
            {"adopted": "已采用", "discarded": "已弃用"}.get(
                row.get("selection"), "未采集"
            )
            + {
                "memory_replacement": " · 补充 Memory 重发",
                "guardrail_replacement": " · 护栏回复重发",
            }.get(row.get("pipeline_role"), "")
        )
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return (
        '<section class="panel compact-panel"><div class="section-heading"><h2>LLM 请求证据</h2></div>'
        '<p class="muted">每行对应一次 HTTP 尝试，工具后的新请求与重试分别关联。Thinking / Unthinking 取自请求的 enable_thinking 配置；未记录时显示未采集。首个完整 SSE 数据块可能只有角色信息；First Token 保留有效文本或工具增量口径。'
        "发送后等待包含网络与服务端处理；服务端等待不等于模型计算。未观测到的连接、Token 和阶段显示未知。</p>"
        '<div class="table-scroll" tabindex="0" aria-label="LLM 请求证据，可水平滚动"><table class="llm-evidence-table"><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in headings)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div></section>"
    )


def build_report(report, path):
    """Write a session overview and independent, fully offline turn pages."""
    export_excel([report], Path(path).with_name("evaluation.xlsx"))
    assets = Path(__file__).parent
    template = (assets / "report_template.html").read_text()
    style = "\n".join(
        (assets / name).read_text()
        for name in ("report.css", "reply_timing.css", "session_timeline.css")
    )
    script = "\n".join(
        (assets / name).read_text()
        for name in (
            "report_navigation.js",
            "media_markers.js",
            "reply_timing.js",
            "timeline_gestures.js",
            "timeline_navigation.js",
            "session_timeline.js",
            "timeline_measurement.js",
            "report.js",
        )
    )
    display = copy.deepcopy(report)
    playback = display.setdefault("session_playback", {})
    refresh_ptt_markers(playback, display["turns"])
    playback["media_markers"] = []
    zero = playback.get("zero_at_ns")
    for index, turn in enumerate(display["turns"], 1):
        turn["media_markers"] = media_timeline_markers(turn.get("events", []))
        if zero is not None:
            playback["media_markers"].extend(
                dict(
                    marker,
                    turn_index=index,
                    at_seconds=(marker["start_ns"] - zero) / 1e9,
                )
                for marker in turn["media_markers"]
            )
        if "reply_timing" not in turn:
            turn["reply_timing"] = analyze_reply_timing(turn)
        turn["timeline_lanes"] = group_timeline_spans(
            turn.get("spans", []), turn.get("vas_events", [])
        )
        turn["combined_timeline"] = combined_timeline(
            turn, display.get("client_clock"), display.get("clock_sync")
        )
    playback["vas_timeline"] = session_trace_timeline(display)
    add_key_moment_lanes(display)
    raw_display = copy.deepcopy(display)
    raw_display.pop("clock_sync", None)
    for raw_turn in raw_display["turns"]:
        raw_turn["combined_timeline"] = combined_timeline(
            raw_turn, raw_display.get("client_clock")
        )
    raw_display["session_playback"]["vas_timeline"] = session_trace_timeline(
        raw_display
    )
    add_key_moment_lanes(raw_display)
    environment = display.get("run_metadata", {}).get("environment", "本地报告")

    def page(index):
        turn = display["turns"][index] if index is not None else None
        playback = display.get("session_playback", {})
        if index is not None:
            playback = _turn_playback(playback, turn, index + 1)
        raw_playback = raw_display["session_playback"]
        if index is not None:
            raw_playback = _turn_playback(
                raw_playback, raw_display["turns"][index], index + 1
            )
        sync = display.get("clock_sync", {})
        sync_label = "未校准（旧报告、未启用或服务不支持）"
        if sync.get("status") == "calibrated":
            sync_label = f"跨端已校准 · 偏移 {sync['offset_ns']/1e9:+.6f} s · 采样不确定范围 ±{sync['uncertainty_ns']/1e9:.6f} s"
        elif sync.get("status") == "unstable":
            sync_label = "校准不稳定，已保留原始时间；请检查时钟变化或连接归属"
        notice = f'<div class="notice"><span id="clock-sync-status">{_text(sync_label)}</span> · <a href="?clock=raw">原始时间</a> / <a href="?clock=calibrated">校准时间</a></div>'
        encoded = (
            json.dumps(
                {
                    "clock_sync": sync,
                    "raw_view": {
                        "turn": (
                            raw_display["turns"][index] if index is not None else None
                        ),
                        "session_playback": raw_playback,
                    },
                    "turn": turn,
                    "turn_index": index + 1 if index is not None else None,
                    "session_playback": playback,
                },
                ensure_ascii=False,
            )
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
            "__CONTENT__": notice
            + (_turn_page(display, index) if turn is not None else _overview(display)),
            "__REPORT_DATA__": encoded,
            "__SCRIPT__": script,
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
