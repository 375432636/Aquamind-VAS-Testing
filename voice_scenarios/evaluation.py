"""Small persona test contract and client-playback metrics for the Excel report."""

from collections import Counter

from .reply_timing import analyze_reply_timing

# Exact runtime names, not substring matches against the LLM's reply text.
TOOL_NAMES = {
    "知识库-文字": ("rag-lightrag_search",),
    "知识库-图文": ("rag-lightrag_search",),
    "知识库-视频": ("rag-lightrag_search",),
    "MCP-占星": (),  # An unverified name must not silently pass.
    "MCP-黄历": ("Tung-Shing-get-tung-shing",),
    "MCP-抽签": ("DrawLots-drawLot",),
    "MCP-天气": ("get_weather",),
    "MCP-新闻": ("News-getTodayNewsByTopic",),
    "MCP-音乐": ("play_music", "self_music_play"),
    "Skill": ("invoke_skill",),
}
NO_TOOL_CHECK = {"人设", "Prompt", "LLM"}
HEADERS = [
    "形象",
    "知识库数量",
    "测试重点",
    "调用工具",
    "问题",
    "是否调用了正确的工具",
    "首次回复时间（秒）",
    "临时回复结束到正式回复开始（秒）",
]
STATUS_LABELS = {
    "passed": "是",
    "failed": "否",
    "unknown": "未采集",
    "not_applicable": "不检查",
}


def validate_evaluation(value):
    if not isinstance(value, dict) or set(value) != {
        "persona",
        "knowledge_base_count",
        "focus",
    }:
        raise ValueError("evaluation requires persona, knowledge_base_count and focus")
    if (
        not isinstance(value["persona"], str)
        or not 1 <= len(value["persona"].strip()) <= 160
    ):
        raise ValueError("evaluation.persona must contain 1–160 characters")
    if (
        type(value["knowledge_base_count"]) is not int
        or value["knowledge_base_count"] < 0
    ):
        raise ValueError(
            "evaluation.knowledge_base_count must be a nonnegative integer"
        )
    if not isinstance(value["focus"], str) or len(value["focus"]) > 160:
        raise ValueError("evaluation.focus must be text up to 160 characters")
    return dict(value)


def validate_tool(value):
    if not isinstance(value, str) or value not in NO_TOOL_CHECK | TOOL_NAMES.keys():
        raise ValueError(
            "tool must be 人设, Prompt, LLM, Skill, 知识库-文字/图文/视频 or MCP-占星/黄历/抽签/天气/新闻/音乐"
        )
    return value


def tool_check(category, events, complete):
    validate_tool(category)
    wanted = TOOL_NAMES.get(category, ())
    calls = [event for event in events if event.get("event") == "tool_call_started"]
    actual = dict(Counter(event.get("data", {}).get("tool_name") for event in calls))
    result = {
        "name": "persona.tools",
        "category": "tools",
        "expected": {"any_of": list(wanted)},
        "actual": {"calls": actual},
        "passed": False,
        "failure_kind": None,
    }
    if category in NO_TOOL_CHECK:
        status, reason = "not_applicable", "此问题不检查工具调用"
    elif not wanted:
        status, reason = "unknown", "工具名称尚未核实"
        result["failure_kind"] = "configuration_unverified"
    else:
        matched = [
            call for call in calls if call.get("data", {}).get("tool_name") in wanted
        ]
        ends = {
            e.get("span_id"): e
            for e in events
            if e.get("event") == "tool_call_finished" and e.get("span_id")
        }
        statuses = [ends.get(call.get("span_id"), {}).get("status") for call in matched]
        if "ok" in statuses:
            status, reason = "passed", "观察到预期工具正常完成"
        elif matched and all(state is not None for state in statuses):
            status, reason = "failed", "预期工具执行失败或取消"
        elif matched or complete is not True:
            status, reason = "unknown", "诊断不完整或缺少工具结束事件"
        else:
            status, reason = "failed", "本轮未调用预期工具"
    result.update(
        status=status, reason=reason, passed=status in {"passed", "not_applicable"}
    )
    if not result["passed"] and not result["failure_kind"]:
        result["failure_kind"] = (
            "diagnostic_missing" if status == "unknown" else "functional"
        )
    return result


def playback_metrics(turn):
    timing = turn.get("reply_timing") or analyze_reply_timing(turn)
    sentences = sorted(
        (
            row
            for row in timing["sentences"]
            if row.get("kind") != "greeting" and row.get("start_seconds") is not None
        ),
        key=lambda row: row["start_seconds"],
    )
    metrics = turn.get("metrics", {})
    first = (
        metrics["first_playback_ms"] / 1000
        if metrics.get("first_playback_ms") is not None
        else (
            "未采集"
            if "first_playback_ms" in metrics
            else sentences[0]["start_seconds"] if sentences else "未采集"
        )
    )
    answer = next((row for row in sentences if row.get("kind") == "answer"), None)
    if answer is None:
        return first, "未采集"
    preceding = [
        row for row in sentences if row["start_seconds"] < answer["start_seconds"]
    ]
    if not preceding:
        return first, "不适用"
    if any(
        row.get("kind") not in {"pre_speech", "filler"}
        or row.get("status") != "completed"
        or row.get("end_seconds") is None
        for row in preceding
    ):
        return first, "未采集"
    return first, max(
        0, answer["start_seconds"] - max(row["end_seconds"] for row in preceding)
    )


def evaluation_rows(report):
    config = report.get("evaluation")
    if not config:
        return []
    captured = {turn["id"]: turn for turn in report.get("turns", [])}
    turns = report.get("evaluation_turns") or report.get("turns", [])
    return [
        [
            config["persona"],
            config["knowledge_base_count"],
            config["focus"],
            turn.get("tool", ""),
            turn.get("input_text", ""),
            (
                "待核实"
                if turn.get("tool_check", {}).get("failure_kind")
                == "configuration_unverified"
                else STATUS_LABELS.get(
                    turn.get("tool_check", {}).get("status"),
                    "未执行" if turn["id"] not in captured else "未采集",
                )
            ),
            *playback_metrics(turn),
        ]
        for planned in turns
        for turn in [captured.get(planned["id"], planned)]
    ]
