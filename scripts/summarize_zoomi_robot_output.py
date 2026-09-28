"""Summarize real Zoomi robot_output session reports without inventing evidence.

Usage: python scripts/summarize_zoomi_robot_output.py \
    --run DEV=artifacts/dev/sessions/.../report.json \
    --run 5090=artifacts/5090/sessions/.../report.json \
    --output artifacts/zoomi-comparison.md
"""

import argparse
import json
from pathlib import Path

BEHAVIORS = ("action", "product_refs", "expression", "navigation")


def _robot_check(turn):
    return next(
        (
            check
            for check in turn.get("checks", [])
            if check.get("name") == "business.robot_output"
        ),
        None,
    )


def _model_evidence(turn):
    events = turn.get("vas_events", [])
    models = list(
        dict.fromkeys(
            event.get("data", {}).get("model")
            for event in events
            if event.get("event") == "llm_request_started"
            and event.get("data", {}).get("model")
        )
    )
    route = next(
        (
            event.get("data", {})
            for event in events
            if event.get("event") == "intent_classification_finished"
        ),
        {},
    )
    return models, route.get("label"), route.get("target_model")


def summarize_turn(turn):
    check = _robot_check(turn)
    actual = check.get("actual", {}) if check else {}
    fields = actual.get("fields", {})
    models, route_label, target_model = _model_evidence(turn)
    expected_tier = turn.get("id", "").rsplit("-", 1)[-1]
    selected_tier = (
        "27b"
        if models and all("27b" in model.lower() for model in models)
        else "8b" if models and all("8b" in model.lower() for model in models) else None
    )
    called = fields.get("called", {}).get("observed")
    behaviors = {
        name: {
            "expected": fields.get(name, {}).get("expected"),
            "observed": fields.get(name, {}).get("observed"),
            "received_count": fields.get(name, {}).get("received_count", 0),
            "requested_values": fields.get(name, {}).get("requested_values", []),
            "received_values": fields.get(name, {}).get("received_values", []),
            "status": fields.get(name, {}).get("status"),
        }
        for name in BEHAVIORS
    }
    rag_calls = sum(
        event.get("event") == "tool_call_started"
        and event.get("data", {}).get("tool_name") == "rag-lightrag_search"
        for event in turn.get("vas_events", [])
    )
    issues = []
    if selected_tier is not None and expected_tier in {"8b", "27b"}:
        if selected_tier != expected_tier:
            issues.append("模型路由不符")
    elif not models:
        issues.append("未采集到 LLM 型号")
    elif selected_tier is None:
        issues.append("当前不是本地 8B/27B")
    if called is False:
        issues.append("未调用 robot_output")
    elif called is None:
        issues.append("函数调用证据不足")
    if actual.get("validation") == "failed":
        issues.append("VAS 协议校验失败")
    for name, field in behaviors.items():
        if field["status"] == "failed":
            issues.append(f"{name} 未符合预期")
    if turn.get("status") == "failed":
        issues.append(f"轮次失败：{turn.get('error') or '未知原因'}")
    return {
        "id": turn.get("id"),
        "prompt": turn.get("input_text"),
        "asr_text": turn.get("metrics", {}).get("asr_text"),
        "execution_status": turn.get("execution_status", turn.get("status")),
        "expected_tier": expected_tier,
        "selected_tier": selected_tier,
        "models": models,
        "route_label": route_label,
        "target_model": target_model,
        "robot_output_supported": actual.get("supported"),
        "robot_output_called": called,
        "call_count": actual.get("call_count", 0),
        "validation": actual.get("validation", "unknown"),
        "rag_calls": rag_calls,
        "behaviors": behaviors,
        "issues": issues,
    }


def summarize_run(label, path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "label": label,
        "report_path": str(Path(path).resolve()),
        "status": report.get("status"),
        "diagnostics_complete": report.get("diagnostics", {}).get("complete"),
        "turns": [summarize_turn(turn) for turn in report.get("turns", [])],
    }


def _shown(value):
    return "是" if value is True else "否" if value is False else "未知"


def render_markdown(runs):
    lines = [
        "# Zoomi robot_output 冒烟测试结果",
        "",
        "`未知` 表示缺少 VAS 函数调用诊断；即使客户端收到动作或图片，也不能据此证明来自 `robot_output`。",
        "",
    ]
    for run in runs:
        lines.extend(
            [
                f"## {run['label']}",
                "",
                f"报告：[逐轮原始证据]({Path(run['report_path']).with_name('report.html')}) · "
                f"诊断完整：{_shown(run['diagnostics_complete'])}",
                "",
                "| 场景 | 预期 / 实际模型 | 分类 | 调用 | 校验 | RAG | 动作 | 商品图 | 表情 | 导航 | 问题 |",
                "| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
            ]
        )
        for turn in run["turns"]:
            behavior = turn["behaviors"]

            def cell(name):
                field = behavior[name]
                return f"{_shown(field['observed'])} (WS {field['received_count']})"

            lines.append(
                "| "
                + " | ".join(
                    [
                        turn["id"] or "—",
                        f"{turn['expected_tier']} / {','.join(turn['models']) or '—'}",
                        turn["route_label"] or "—",
                        _shown(turn["robot_output_called"]),
                        turn["validation"],
                        str(turn["rag_calls"]),
                        cell("action"),
                        cell("product_refs"),
                        cell("expression"),
                        cell("navigation"),
                        "、".join(turn["issues"]) or "—",
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="append", required=True, metavar="NAME=REPORT_JSON"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = []
    for value in args.run:
        label, separator, path = value.partition("=")
        if not separator or not label or not Path(path).is_file():
            parser.error("--run requires NAME=existing/report.json")
        runs.append(summarize_run(label, path))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(runs), encoding="utf-8")
    args.output.with_suffix(".json").write_text(
        json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
