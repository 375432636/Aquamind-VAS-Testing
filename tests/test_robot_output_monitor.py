import asyncio
import json

import pytest

from voice_scenarios.ci_run import _validate_expect
from voice_scenarios.interactive_session import InteractiveSession
from voice_scenarios.report import _robot_output_panel
from voice_scenarios.robot_output_monitor import (
    evaluate_robot_output,
    validate_robot_output,
)


def row(event, **data):
    return {"event": event, "seq": 7, "data": data}


def control(event, **data):
    return {"event": event, "at_ns": 123, "data": data}


@pytest.mark.parametrize("spec", [{}, {"action": 1}, {"called": "yes"}, {"foo": True}])
def test_invalid_monitor_configuration(spec):
    with pytest.raises(ValueError):
        validate_robot_output(spec)


def test_automated_scenario_requires_diagnostics():
    with pytest.raises(ValueError, match="requires stage/frame"):
        _validate_expect({"business": {"robot_output": {"called": True}}}, "off")
    _validate_expect({"business": {"robot_output": {"called": True}}}, "stage")


def test_all_four_behaviors_need_valid_call_and_matching_client_messages():
    rows = [
        row("robot_output_monitor_ready", enabled=True),
        row(
            "robot_output_evaluated",
            valid=True,
            reason="ok",
            expression="happy",
            action="wave",
            product_refs=["headphones"],
            navigation={"action": "start", "zone_id": "zone-1"},
        ),
    ]
    events = [
        control("expression", expression={"key": "happy", "gif_id": 3}),
        control("action", state="start", action_name="wave"),
        control(
            "display", items=[{"kind": "image", "url": "https://example.test/a.png"}]
        ),
        control("navigation", navigation={"action": "start", "zone_id": "zone-1"}),
    ]
    spec = dict(
        called=True, action=True, product_refs=True, expression=True, navigation=True
    )
    check = evaluate_robot_output(
        {"events": events}, rows, spec, diagnostics_complete=True
    )
    assert check["status"] == "passed"
    assert all(x["status"] == "passed" for x in check["actual"]["fields"].values())
    assert check["actual"]["fields"]["product_refs"]["received"] is True


def test_plain_reply_and_unrelated_controls_cannot_prove_robot_output():
    spec = {"called": True, "action": True}
    check = evaluate_robot_output(
        {"events": [control("action", action_name="wave", state="start")]},
        [row("robot_output_monitor_ready", enabled=True)],
        spec,
        diagnostics_complete=True,
    )
    assert check["status"] == "failed"
    assert check["actual"]["fields"]["called"]["observed"] is False
    assert check["actual"]["fields"]["action"]["received"] is True
    assert check["actual"]["fields"]["action"]["observed"] is False


def test_legacy_vas_without_ready_marker_is_unknown_even_with_complete_diagnostics():
    check = evaluate_robot_output(
        {"events": []}, [], {"called": True}, diagnostics_complete=True
    )
    assert check["status"] == "unknown"
    assert check["failure_kind"] == "diagnostic_missing"


def test_invalid_call_and_missing_image_are_distinct_failures():
    rows = [
        row("robot_output_monitor_ready", enabled=True),
        row("robot_output_evaluated", valid=False, reason="schema_invalid"),
    ]
    bad = evaluate_robot_output(
        {"events": []}, rows, {"called": True}, diagnostics_complete=True
    )
    assert bad["actual"]["fields"]["called"]["observed"] is True
    assert bad["actual"]["validation"] == "failed"
    assert bad["status"] == "failed"

    rows[-1] = row(
        "robot_output_evaluated", valid=True, reason="ok", product_refs=["p1"]
    )
    missing = evaluate_robot_output(
        {"events": []},
        rows,
        {"called": True, "product_refs": True},
        diagnostics_complete=True,
    )
    assert missing["actual"]["fields"]["product_refs"]["observed"] is False
    assert missing["status"] == "failed"


def test_navigation_stop_and_forbidden_action():
    rows = [
        row("robot_output_monitor_ready", enabled=True),
        row(
            "robot_output_evaluated",
            valid=True,
            reason="ok",
            navigation={"action": "stop"},
            action="none",
        ),
    ]
    check = evaluate_robot_output(
        {"events": [control("navigation", navigation={"action": "stop"})]},
        rows,
        {"navigation": True, "action": False},
        diagnostics_complete=True,
    )
    assert check["status"] == "passed"


def test_incomplete_diagnostics_cannot_prove_absence():
    check = evaluate_robot_output(
        {"events": []},
        [row("robot_output_monitor_ready", enabled=True)],
        {"called": False, "action": False},
        diagnostics_complete=False,
    )
    assert check["status"] == "unknown"


def test_generic_image_cannot_be_attributed_to_product_refs():
    rows = [
        row("robot_output_monitor_ready", enabled=True),
        row("robot_output_evaluated", valid=True, reason="ok", product_refs=["p1"]),
    ]
    check = evaluate_robot_output(
        {"events": [control("image", url="https://example.test/a.png")]},
        rows,
        {"product_refs": True},
        diagnostics_complete=True,
    )
    assert check["status"] == "unknown"
    assert check["actual"]["fields"]["product_refs"]["unattributed_image_count"] == 1


def test_monitor_only_reports_observation_without_failing():
    check = evaluate_robot_output(
        {"events": []},
        [row("robot_output_monitor_ready", enabled=True)],
        {"monitor": True},
        diagnostics_complete=True,
    )
    assert check["status"] == "not_applicable"
    assert check["actual"]["fields"]["called"]["observed"] is False


def test_live_session_copies_monitor_configuration_into_each_turn(tmp_path):
    config = {
        "environment": "dev",
        "device_id": "00:00:00:00:00:21",
        "robot_output": {"monitor": True, "called": True, "action": False},
    }
    session = InteractiveSession(tmp_path, config)
    session.accept(
        {
            "event": "turn_started",
            "at_ns": 1,
            "turn": 1,
            "data": {"mode": "text", "text": "请挥手"},
        }
    )
    assert (
        session.result["turns"][0]["expected"]["business"]["robot_output"]
        == config["robot_output"]
    )
    asyncio.run(session.finish(False, render=False))


def test_report_panel_shows_function_validation_and_four_behaviors():
    rows = [
        row("robot_output_monitor_ready", enabled=True),
        row(
            "robot_output_evaluated",
            valid=True,
            reason="ok",
            expression="happy",
            action="wave",
            product_refs=["p1"],
            navigation={"zone_id": "zone-1"},
        ),
    ]
    check = evaluate_robot_output(
        {"events": [control("action", state="start", action_name="wave")]},
        rows,
        {"monitor": True, "called": True, "action": True},
        diagnostics_complete=True,
    )
    html = _robot_output_panel({"checks": [check]})
    assert "Robot output 监测" in html
    assert "至少一次调用通过 VAS 校验" in html
    for label in (
        "函数 robot_output",
        "动作 action",
        "商品图片 product_refs",
        "表情 expression",
        "导航 navigation",
    ):
        assert label in html
    assert "客户端收到 1 条" in html


def test_live_report_uses_recorded_diagnostics_and_websocket_control(tmp_path):
    config = {
        "environment": "dev",
        "device_id": "00:00:00:00:00:21",
        "robot_output": {"called": True, "action": True, "expression": False},
    }
    session = InteractiveSession(tmp_path, config)

    def send(name, when, turn=0, **data):
        session.accept({"event": name, "at_ns": when, "turn": turn, "data": data})

    send("hello_received", 1, session_id="session-1")
    send("turn_started", 2, 1, mode="text", text="请挥手", server_listen_turn_id=7)
    send("action", 3, 1, action_name="wave", state="start")
    send("turn_finished", 4, 1)
    diagnostic_rows = [
        {
            "schema_version": 1,
            "server_session_id": "session-1",
            "listen_turn_id": 7,
            "clock_id": "vas",
            "monotonic_ns": seq * 1000,
            "seq": seq,
            "event": name,
            "data": data,
        }
        for seq, (name, data) in enumerate(
            [
                ("robot_output_monitor_ready", {"enabled": True}),
                (
                    "robot_output_evaluated",
                    {
                        "valid": True,
                        "reason": "ok",
                        "action": "wave",
                        "expression": "none",
                        "product_refs": [],
                        "navigation": None,
                    },
                ),
            ],
            1,
        )
    ]
    send(
        "diagnostics",
        5,
        schema_version=1,
        server_session_id="session-1",
        events=diagnostic_rows,
        next_seq=2,
        end_seq=2,
        complete=True,
        finished=True,
    )
    report = asyncio.run(session.finish(render=True))
    check = next(
        c for c in report["turns"][0]["checks"] if c["name"] == "business.robot_output"
    )
    assert report["diagnostics"]["complete"] is True
    assert check["status"] == "passed"
    assert check["actual"]["fields"]["action"]["observed"] is True
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["turns"][0]["checks"][-1]["name"] == "business.robot_output"
    assert "Robot output 监测" in (tmp_path / "turn-001.html").read_text()
