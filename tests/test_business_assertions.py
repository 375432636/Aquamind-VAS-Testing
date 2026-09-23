import copy
import json
import wave

import pytest
import yaml

from voice_scenarios.assertions import (
    evaluate_business_assertions,
    validate_business_assertions,
)
from voice_scenarios.ci_run import ROOT, settings_from_env, validate_turns
from voice_scenarios.failure_summary import failure_reasons
from voice_scenarios.model import Scenario
from voice_scenarios.report import evaluate as evaluate_report


def wire(name, **data):
    return {"event": name, "at_ns": 1, "data": data}


def turn_fixture(tmp_path, *, text="今天有什么新闻", reply="今日要闻：科技新闻"):
    path = tmp_path / "received.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"\x01\x10" * 160)
    return {
        "id": "news",
        "listen_turn_id": 1,
        "status": "completed",
        "audio": {"received": str(path)},
        "expected": {
            "business": {
                "recognition": {"contains_all": [["新闻", "要闻", "资讯"]]},
                "tools": {
                    "required": [{"name": "news", "arguments": {"topic": "today"}}],
                    "forbidden": ["DrawLots-drawLot"],
                },
                "reply": {"contains_all": [["新闻", "要闻", "资讯"]]},
                "audio": {"ending": "normal"},
            }
        },
        "events": [
            wire("stt", text=text),
            wire("tts_sentence_start", text=reply, response_listen_turn_id=1),
            wire("audio_packet_received", audio_seq=1, bytes=20),
            wire(
                "audio_received",
                audio_seq=1,
                bytes=320,
                sample_rate=16000,
                duration_ms=10,
                output_kind="answer",
                response_listen_turn_id=1,
            ),
            wire("tts_stop", response_listen_turn_id=1),
            wire("playback_drained"),
        ],
    }


def tool_rows(name="news", arguments=None, status="ok"):
    return [
        {
            "event": "tool_call_started",
            "span_id": "tool-1",
            "data": {
                "tool_name": name,
                **({"arguments": arguments} if arguments is not None else {}),
            },
        },
        {
            "event": "tool_call_finished",
            "span_id": "tool-1",
            "status": status,
            "data": {},
        },
    ]


def evaluate(turn, rows=None, complete=True, **kwargs):
    return {
        item["category"]: item
        for item in evaluate_business_assertions(
            turn,
            tool_rows(arguments={"topic": "today"}) if rows is None else rows,
            diagnostics_complete=complete,
            **kwargs,
        )
    }


@pytest.mark.parametrize(
    ("reply", "passed"),
    [
        ("今天的星象关键词是清醒的混乱。", True),
        ("太阳处女与火星六合，月亮天秤合金星，水星逆行。", True),
        ("今天有几条新闻资讯，请稍后查看。", False),
    ],
)
def test_smoke_news_checks_astrology_in_the_formal_reply(tmp_path, reply, passed):
    scenario = yaml.safe_load(
        (ROOT / "scenarios/smoke/03-news-almanac-interaction-products.yaml").read_text()
    )
    news = next(turn for turn in scenario["turns"] if turn["id"] == "news")
    turn = turn_fixture(tmp_path, reply=reply)
    turn["expected"] = news["expect"]
    assert evaluate(turn)["reply"]["passed"] is passed


def test_semantic_synonyms_required_tool_key_arguments_and_decodable_reply(tmp_path):
    turn = turn_fixture(tmp_path, text="给我讲讲今日要闻", reply="今日资讯：科技新动态")
    assert all(check["passed"] for check in evaluate(turn).values())


def test_news_misrecognized_as_heart_wrong_tool_cannot_pass_successful_llm_tts(
    tmp_path,
):
    turn = turn_fixture(
        tmp_path, text="帮我看一下今天的心", reply="抽到了上吉签，今日运势很好"
    )
    turn["metrics"] = {"llm_requests": 2, "tts_requests": 2}
    checks = evaluate(turn, tool_rows("DrawLots-drawLot", {}))
    assert checks["audio"]["passed"]
    for category in ("recognition", "tools", "reply"):
        assert checks[category]["status"] == "failed"
        assert checks[category]["failure_kind"] == "functional"
        assert checks[category]["reason"]


def test_missing_diagnostics_does_not_prove_forbidden_tools_absent(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["expected"]["business"]["tools"] = {"forbidden": ["DrawLots-drawLot"]}
    checks = evaluate(turn, [], complete=False)
    assert checks["recognition"]["passed"]
    assert checks["audio"]["passed"]
    assert checks["tools"]["status"] == "unknown"
    assert checks["tools"]["failure_kind"] == "diagnostic_missing"


@pytest.mark.parametrize(
    "arguments,kind",
    [(None, "diagnostic_missing"), ({"topic": "tomorrow"}, "functional")],
)
def test_missing_tool_argument_is_distinct_from_wrong_argument(
    tmp_path, arguments, kind
):
    check = evaluate(turn_fixture(tmp_path), tool_rows(arguments=arguments))["tools"]
    assert not check["passed"]
    assert check["failure_kind"] == kind


def test_observed_wrong_tool_fails_even_if_diagnostics_incomplete(tmp_path):
    check = evaluate(
        turn_fixture(tmp_path), tool_rows("DrawLots-drawLot"), complete=False
    )["tools"]
    assert check["status"] == "failed"
    assert check["failure_kind"] == "functional"


def test_failed_tool_execution_is_a_functional_failure(tmp_path):
    check = evaluate(
        turn_fixture(tmp_path), tool_rows(arguments={"topic": "today"}, status="error")
    )["tools"]
    assert check["failure_kind"] == "functional"


def test_unverified_device_expectation_is_unknown_not_a_pass(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["expected"]["business"]["tools"] = {
        "verified": False,
        "reason": "当前设备新闻能力未核实",
    }
    check = evaluate(turn)["tools"]
    assert check["status"] == "unknown"
    assert check["failure_kind"] == "configuration_unverified"


def test_no_mandatory_or_forbidden_tools_is_explicitly_not_applicable(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["expected"]["business"]["tools"] = {"required": [], "forbidden": []}
    assert evaluate(turn)["tools"]["status"] == "not_applicable"


def test_sensor_checks_actual_command_without_claiming_asr(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["sensor"] = "touch-head"
    turn["events"].insert(0, wire("sensor_sent", mode="touch-head"))
    turn["expected"]["business"]["recognition"] = {"sensor": "touch-head"}
    check = evaluate(turn)["recognition"]
    assert check["passed"]
    assert check["actual"]["source"] == "sensor"
    turn["sensor"] = "touch-hand"
    assert evaluate(turn)["recognition"]["status"] == "failed"


def test_sensor_requires_sent_evidence_and_rejects_wrong_server_echo(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["sensor"] = "touch-head"
    turn["expected"]["business"]["recognition"] = {"sensor": "touch-head"}
    assert evaluate(turn)["recognition"]["failure_kind"] == "diagnostic_missing"
    turn["events"].insert(0, wire("sensor_sent", mode="touch-head"))
    turn["server_input_text"] = (
        '<physical_interaction><detected_action sensor_name="touch-hand">手</detected_action></physical_interaction>'
    )
    assert evaluate(turn)["recognition"]["failure_kind"] == "functional"


def test_greeting_and_pre_speech_do_not_satisfy_reply(tmp_path):
    turn = turn_fixture(tmp_path, reply="抽签结果很好")
    turn["events"][:0] = [
        wire("tts_sentence_start", text="新闻", is_session_output=True),
        wire("tts_stop", is_session_output=True),
    ]
    for event in turn["events"]:
        if event["event"] == "audio_received":
            event["data"]["output_kind"] = "pre_speech"
    assert not evaluate(turn)["reply"]["passed"]


def test_missing_answer_attribution_is_unknown(tmp_path):
    turn = turn_fixture(tmp_path)
    for event in turn["events"]:
        event["data"].pop("output_kind", None)
    check = evaluate(turn)["reply"]
    assert check["failure_kind"] == "diagnostic_missing"
    turn["expected"]["business"]["reply"]["output_kind"] = "any"
    assert evaluate(turn)["reply"]["passed"]


@pytest.mark.parametrize(
    "variant", ["empty", "corrupt", "truncated", "greeting_only", "other_turn_only"]
)
def test_audio_rejects_invalid_empty_or_unowned_reply(tmp_path, variant):
    turn = turn_fixture(tmp_path)
    path = tmp_path / "received.wav"
    if variant == "empty":
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
    elif variant == "corrupt":
        path.write_bytes(b"not a wav")
    elif variant == "truncated":
        path.write_bytes(path.read_bytes()[:-4])
    else:
        for event in turn["events"]:
            if event["event"] == "audio_received":
                event["data"].update(
                    {"is_session_output": True}
                    if variant == "greeting_only"
                    else {"response_listen_turn_id": 2}
                )
    assert evaluate(turn)["audio"]["failure_kind"] == "functional"


def test_missing_audio_artifact_is_capture_failure_not_business_failure(tmp_path):
    turn = turn_fixture(tmp_path)
    (tmp_path / "received.wav").unlink()
    assert evaluate(turn)["audio"]["failure_kind"] == "diagnostic_missing"


def test_moved_artifact_is_resolved_from_report_directory(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["audio"]["received"] = "/old/location/received.wav"
    assert evaluate(turn, artifact_dir=tmp_path)["audio"]["passed"]


def test_expected_interruption_requires_abort_and_stop_ack(tmp_path):
    turn = turn_fixture(tmp_path)
    turn["expected"]["business"]["audio"]["ending"] = "interrupted"
    assert not evaluate(turn)["audio"]["passed"]
    turn.update(
        status="interrupted",
        requested_interruption={"after_playback_seconds": 1},
        interruption={
            "abort_requested_at_ns": 10,
            "client_playback_stopped": True,
            "server_stop_observed": True,
        },
    )
    assert evaluate(turn)["audio"]["passed"]
    turn["interruption"]["server_stop_observed"] = False
    assert evaluate(turn)["audio"]["status"] == "failed"


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"wat": {}},
        {"recognition": {"contains_all": []}},
        {"reply": {"contains_all": [[""]]}},
        {"tools": {"required": ["news"]}},
        {"tools": {"required": [{"name": "news", "arguments": {"x": {"nested": 1}}}]}},
        {"audio": {"ending": "success"}},
        {"audio": {"min_duration_ms": True}},
        {"tools": {"verified": False}},
    ],
)
def test_invalid_business_configuration_rejected(bad):
    with pytest.raises(ValueError):
        validate_business_assertions(bad)


def test_business_configuration_survives_both_scenario_loading_paths(tmp_path):
    turn = turn_fixture(tmp_path)
    spec = copy.deepcopy(turn["expected"])
    settings = settings_from_env(
        {"VAS_DEVICE_ID": "30:ED:A0:A6:23:A4", "VAS_DIAGNOSTICS": "off"}
    )
    normalized = validate_turns(
        json.dumps([{"text": "今天新闻", "expect": spec}]), settings
    )
    assert normalized[0]["expect"] == spec
    scenario = Scenario.from_dict(
        {"turns": [{"audio": str(tmp_path / "received.wav"), "expect": spec}]}
    )
    assert scenario.turns[0].expect == spec
    spec["business"]["audio"]["ending"] = "bad"
    with pytest.raises(ValueError):
        Scenario.from_dict(
            {"turns": [{"audio": str(tmp_path / "received.wav"), "expect": spec}]}
        )


def test_legacy_scenarios_have_no_new_assertions():
    assert evaluate_business_assertions({"expected": {"llm_requests_min": 1}}, []) == []


def test_business_ci_failure_summary_omits_conversation_and_argument_values():
    check = {
        "name": "business.reply",
        "passed": False,
        "failure_kind": "functional",
        "reason": "缺少必要语义组：新闻",
        "expected": {"contains_all": [["新闻"]]},
        "actual": {"text": "private conversation"},
    }
    summary = failure_reasons(
        {"status": "failed", "turns": [{"id": "news", "checks": [check]}]}
    )
    assert "functional" in summary[0]
    assert "private conversation" not in summary[0]


def test_report_verdict_fails_wrong_news_despite_passing_llm_tts_and_audio(tmp_path):
    turn = turn_fixture(tmp_path, text="帮我看一下今天的心", reply="抽到了上吉签")
    turn["expected"].update(
        llm_requests_min=1, tts_requests_min=1, max_first_playback_ms=10
    )
    turn["events"][:0] = [
        wire("listen_stop_sent"),
        {**wire("playback_started"), "at_ns": 20_000_001},
    ]
    rows = tool_rows("DrawLots-drawLot", {}) + [
        {"event": "llm_request_started", "data": {}},
        {"event": "tts_request_started", "data": {}},
    ]
    for seq, row in enumerate(rows):
        row.update(listen_turn_id=1, clock_id="vas", monotonic_ns=seq, seq=seq + 1)
    report = evaluate_report(
        {
            "name": "negative",
            "status": "passed",
            "diagnostics": {"complete": True},
            "turns": [turn],
        },
        rows,
    )
    assert report["status"] == "failed"
    checks = {check["name"]: check for check in report["turns"][0]["checks"]}
    assert checks["llm_requests_min"]["passed"]
    assert checks["tts_requests_min"]["passed"]
    assert checks["business.audio"]["passed"]
    assert not checks["business.recognition"]["passed"]
    assert not checks["business.tools"]["passed"]
    assert not checks["business.reply"]["passed"]
    assert report["failure_groups"]["functional"]
    assert report["failure_groups"]["latency"]
    assert not report["failure_groups"]["diagnostic_missing"]
