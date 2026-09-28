"""The two saved Zoomi suites must stay comparable and report uncertainty honestly."""

from pathlib import Path

import yaml

from scripts.summarize_zoomi_robot_output import summarize_turn
from voice_scenarios.batch_run import load_sessions

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios" / "robot-output-zoomi"


def test_dev_and_5090_cover_ten_matching_intents_with_valid_local_zones():
    dev = yaml.safe_load((SCENARIOS / "01-dev.yaml").read_text())
    local = yaml.safe_load((SCENARIOS / "02-5090.yaml").read_text())
    assert len(dev["turns"]) == len(local["turns"]) == 10
    assert dev["device_id"] != local["device_id"]
    assert [turn["id"] for turn in dev["turns"]] == [
        turn["id"] for turn in local["turns"]
    ]
    assert [turn["expect"] for turn in dev["turns"]] == [
        turn["expect"] for turn in local["turns"]
    ]
    assert [turn["text"] for turn in dev["turns"][:7]] == [
        turn["text"] for turn in local["turns"][:7]
    ]
    assert dev["turns"][9]["text"] == local["turns"][9]["text"]
    assert "产品体验区" in dev["turns"][7]["text"]
    assert "充电桩" in dev["turns"][8]["text"]
    assert "数码体验区" in local["turns"][7]["text"]
    assert "顾客服务台" in local["turns"][8]["text"]
    assert {"8b", "27b"} == {turn["id"].rsplit("-", 1)[-1] for turn in dev["turns"]}
    assert all(
        turn["expect"]["business"]["robot_output"]["called"] is True
        for turn in dev["turns"]
    )
    for field, count in (
        ("action", 2),
        ("product_refs", 4),
        ("expression", 3),
        ("navigation", 2),
    ):
        assert (
            sum(
                turn["expect"]["business"]["robot_output"].get(field) is True
                for turn in dev["turns"]
            )
            == count
        )
    assert len(load_sessions(SCENARIOS, {"VAS_DIAGNOSTICS": "frame"})) == 2


def test_summary_does_not_claim_no_call_when_old_vas_lacks_marker():
    turn = {
        "id": "basic-wave-8b",
        "status": "completed",
        "events": [{"event": "action", "data": {"action_name": "挥手"}}],
        "vas_events": [
            {"event": "llm_request_started", "data": {"model": "qwen3.8-max"}}
        ],
        "checks": [
            {
                "name": "business.robot_output",
                "actual": {
                    "supported": False,
                    "call_count": 0,
                    "validation": "unknown",
                    "fields": {
                        "called": {"observed": None},
                        "action": {
                            "observed": None,
                            "received_count": 1,
                            "status": "unknown",
                        },
                    },
                },
            }
        ],
    }
    summary = summarize_turn(turn)
    assert summary["robot_output_called"] is None
    assert summary["behaviors"]["action"]["observed"] is None
    assert summary["behaviors"]["action"]["received_count"] == 1
    assert "函数调用证据不足" in summary["issues"]
    assert "未调用 robot_output" not in summary["issues"]


def test_summary_exposes_smart_classified_as_8b_with_missing_product_image():
    turn = {
        "id": "product-waterproof-27b",
        "status": "completed",
        "vas_events": [
            {
                "event": "intent_classification_finished",
                "data": {"label": "smart", "target_model": "ro-test-8b"},
            },
            {"event": "llm_request_started", "data": {"model": "ro-test-8b"}},
            {
                "event": "tool_call_started",
                "data": {"tool_name": "rag-lightrag_search"},
            },
        ],
        "checks": [
            {
                "name": "business.robot_output",
                "actual": {
                    "supported": True,
                    "call_count": 1,
                    "validation": "passed",
                    "fields": {
                        "called": {"observed": True},
                        "product_refs": {
                            "expected": True,
                            "observed": False,
                            "received_count": 0,
                            "status": "failed",
                        },
                    },
                },
            }
        ],
    }
    summary = summarize_turn(turn)
    assert summary["route_label"] == "smart"
    assert summary["selected_tier"] == "8b"
    assert summary["rag_calls"] == 1
    assert "模型路由不符" in summary["issues"]
    assert "product_refs 未符合预期" in summary["issues"]
