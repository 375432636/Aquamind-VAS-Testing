import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from voice_scenarios.batch_run import load_sessions
from voice_scenarios.evaluation import evaluation_rows, tool_check
from voice_scenarios.excel_report import export_excel


def calls(name, status="ok"):
    return [
        {"event": "tool_call_started", "span_id": "one", "data": {"tool_name": name}},
        {"event": "tool_call_finished", "span_id": "one", "status": status, "data": {}},
    ]


@pytest.mark.parametrize("category", ["人设", "Prompt", "LLM"])
def test_prompt_and_llm_do_not_require_tool_evidence(category):
    assert tool_check(category, [], False)["status"] == "not_applicable"
    assert tool_check(category, calls("unexpected"), True)["status"] == "not_applicable"


def test_tool_verdict_distinguishes_wrong_failed_and_missing_evidence():
    assert (
        tool_check("知识库-图文", calls("rag-lightrag_search"), True)["status"]
        == "passed"
    )
    assert tool_check("知识库-视频", calls("invoke_skill"), True)["status"] == "failed"
    assert (
        tool_check("Skill", calls("invoke_skill", "error"), True)["status"] == "failed"
    )
    assert tool_check("Skill", calls("invoke_skill")[:1], True)["status"] == "unknown"
    assert tool_check("Skill", [], False)["status"] == "unknown"
    assert tool_check("Skill", [], True)["status"] == "failed"


def report_fixture():
    return {
        "name": "测试人设",
        "evaluation": {
            "persona": "Zoomi（粉）",
            "knowledge_base_count": 4,
            "focus": "产品出图情况",
        },
        "run_metadata": {"environment": "main", "device_id": "00:00:00:00:00:21"},
        "turns": [
            {
                "id": "one",
                "input_text": "你是谁",
                "tool": "人设",
                "reply_timing": {
                    "sentences": [
                        {
                            "kind": "greeting",
                            "start_seconds": -2,
                            "end_seconds": 1,
                            "status": "completed",
                        },
                        {
                            "kind": "pre_speech",
                            "start_seconds": 2,
                            "end_seconds": 3,
                            "status": "completed",
                        },
                        {
                            "kind": "filler",
                            "start_seconds": 4,
                            "end_seconds": 5,
                            "status": "completed",
                        },
                        {
                            "kind": "answer",
                            "start_seconds": 7,
                            "end_seconds": 10,
                            "status": "completed",
                        },
                    ]
                },
                "tool_check": {"status": "not_applicable"},
            }
        ],
    }


def test_timing_excludes_greeting_and_measures_last_temporary_end_to_answer():
    report = report_fixture()
    row = evaluation_rows(report)[0]
    assert row == ["Zoomi（粉）", 4, "产品出图情况", "人设", "你是谁", "不检查", 2, 2]
    sentences = report["turns"][0]["reply_timing"]["sentences"]
    report["turns"][0]["reply_timing"]["sentences"] = [sentences[-1]]
    assert evaluation_rows(report)[0][-2:] == [7, "不适用"]
    report["turns"][0]["reply_timing"]["sentences"] = sentences[:-1]
    assert evaluation_rows(report)[0][-1] == "未采集"
    sentences[-2]["status"] = "interrupted"
    report["turns"][0]["reply_timing"]["sentences"] = sentences
    assert evaluation_rows(report)[0][-1] == "未采集"


def test_excel_preserves_eight_columns_numeric_seconds_and_literal_text(tmp_path):
    report = report_fixture()
    report["data_source"] = "mock"
    report["turns"][0]["input_text"] = '=HYPERLINK("https://example.com", "click")'
    target = tmp_path / "evaluation.xlsx"
    export_excel([report], target)
    with ZipFile(target) as workbook:
        assert workbook.testzip() is None
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        row = sheet.find('.//s:row[@r="7"]', ns)
        assert len(row) == 8
        assert row[4].get("t") == "inlineStr"
        assert row[4].find("s:f", ns) is None
        assert row[6].get("t") == "n"
        assert float(row[6].find("s:v", ns).text) == 2
        assert "MOCK" in ET.tostring(sheet, encoding="unicode")
        assert sheet.find("s:autoFilter", ns).get("ref") == "A6:H7"


def test_persona_sessions_match_requested_questions_and_distinct_devices():
    sessions = load_sessions(Path("scenarios/smoke/personas"), {})
    assert len(sessions) == 5
    assert len({session["device_id"] for session in sessions}) == 5
    turns = [json.loads(session["env"]["VAS_TURNS_JSON"]) for session in sessions]
    assert [len(items) for items in turns] == [11, 9, 11, 13, 5]
    assert turns[0][-1]["text"] == "哪一家门店周六早上最早开门？"
    assert turns[-1][-1]["text"] == "复习"
    assert all(session["environment"] == "main" for session in sessions)


def test_unexecuted_questions_remain_in_excel():
    report = report_fixture()
    report["evaluation_turns"] = [
        {"id": "one", "tool": "人设", "input_text": "你是谁"},
        {"id": "two", "tool": "知识库-文字", "input_text": "没来得及提问"},
    ]
    rows = evaluation_rows(report)
    assert len(rows) == 2
    assert rows[1][4:] == ["没来得及提问", "未执行", "未采集", "未采集"]


def test_mock_batch_uses_report_pipeline_without_transport(tmp_path, monkeypatch):
    import asyncio
    import os
    import wave

    import voice_scenarios.batch_run as batch_run
    import voice_scenarios.mock_run as mock_run
    from voice_scenarios.model import InputStream, Scenario, Turn

    def prepare(output, env, *, name=None):
        output.mkdir(parents=True)
        source = output / "question.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(b"\x01\x04" * 1600)
        values = json.loads(env["VAS_TURNS_JSON"])
        return {
            "environment": env["VAS_ENVIRONMENT"],
            "device_id": env["VAS_DEVICE_ID"],
        }, Scenario(
            name,
            tuple(
                Turn(f"turn-{i}", source, input_text=v["text"], tool=v["tool"])
                for i, v in enumerate(values, 1)
            ),
            input=InputStream("vad"),
            evaluation=json.loads(env["VAS_EVALUATION_JSON"]),
        )

    async def network_forbidden(*args, **kwargs):
        pytest.fail("Mock mode must not connect to VAS")

    monkeypatch.setattr(batch_run, "prepare", prepare)
    monkeypatch.setattr(batch_run, "execute_prepared", network_forbidden)

    def synthesize(text, output):
        with wave.open(str(output), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(b"\x01\x04" * 6400)

    monkeypatch.setattr(mock_run, "synthesize", synthesize)
    if os.getenv("CI_PERSONA_REPORT_DIR"):
        # CI publishes these deterministic fixtures alongside coverage results.
        tmp_path = Path(os.environ["CI_PERSONA_REPORT_DIR"]).resolve()
    code = asyncio.run(
        batch_run.execute("scenarios/smoke/personas", tmp_path / "run", {}, mock=True)
    )
    batch = json.loads((tmp_path / "run/batch.json").read_text())
    # Astrology's production tool name is deliberately unverified, not invented.
    assert code == 1
    assert batch["data_source"] == "mock"
    assert len(batch["sessions"]) == 5
    reports = [
        json.loads((tmp_path / "run" / s["directory"] / "report.json").read_text())
        for s in batch["sessions"]
    ]
    assert sum(len(r["turns"]) for r in reports) == 49
    assert len({r["session"]["session_id"] for r in reports}) == 5
    assert all(r["data_source"] == "mock" for r in reports)
    for report in reports:
        assert report["session_playback"]["status"] == "ready"
        assert not report["session_playback"]["limitations"]
        assert (
            report["turns"][-1]["mock_history"][-1]["content"]
            == report["turns"][-1]["input_text"]
        )
        assert len(report["turns"][-1]["mock_history"]) == len(report["turns"]) * 2 - 1
        for turn in report["turns"]:
            row = evaluation_rows({**report, "evaluation_turns": [], "turns": [turn]})[
                0
            ]
            assert isinstance(row[-2], (int, float))
            assert row[-1] == "不适用" or isinstance(row[-1], (int, float))
    assert (
        sum(
            c["status"] == "unknown"
            for r in reports
            for t in r["turns"]
            for c in t["checks"]
        )
        == 1
    )
    assert "MOCK" in (tmp_path / "run/index.html").read_text()
    assert (
        "evaluation.xlsx"
        in (
            tmp_path / "run" / batch["sessions"][0]["directory"] / "report.html"
        ).read_text()
    )
    with ZipFile(tmp_path / "run/evaluation.xlsx") as workbook:
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        assert (
            len([r for r in sheet.findall(".//s:row", ns) if int(r.get("r")) >= 7])
            == 49
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"persona": "x", "knowledge_base_count": True, "focus": ""},
        {"persona": "", "knowledge_base_count": 1, "focus": ""},
    ],
)
def test_invalid_evaluation_rejected(value):
    from voice_scenarios.evaluation import validate_evaluation

    with pytest.raises(ValueError):
        validate_evaluation(value)


def test_preparation_keeps_persona_and_tool_fields(tmp_path, monkeypatch):
    import wave

    from voice_scenarios import ci_run

    def synthesize(text, path):
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(b"\x00\x10" * 1600)

    monkeypatch.setattr(ci_run, "synthesize", synthesize)
    session = load_sessions(Path("scenarios/smoke/personas/01-zoomi.yaml"), {})[0]
    _, scenario = ci_run.prepare(tmp_path, session["env"])
    assert scenario.evaluation["persona"] == "Zoomi（粉）"
    assert scenario.turns[3].tool == "知识库-图文"
    assert scenario.turns[3].input_text == "上海有多少家门店"


def test_unknown_astrology_is_not_reported_as_wrong_tool():
    check = tool_check("MCP-占星", calls("some_tool"), True)
    assert check["status"] == "unknown"
    assert check["failure_kind"] == "configuration_unverified"
    report = report_fixture()
    report["turns"][0]["tool_check"] = check
    assert evaluation_rows(report)[0][5] == "待核实"


def test_archived_persona_report_keeps_excel_and_download_link(tmp_path):
    from voice_scenarios.report import build_report
    from voice_scenarios.report_archive import export_session

    source = tmp_path / "source"
    source.mkdir()
    report = report_fixture()
    report.update(status="passed", data_source="mock", turns=[])
    (source / "report.json").write_text(json.dumps(report))
    build_report(report, source / "report.html")
    export_session(source, tmp_path / "download")
    assert (tmp_path / "download/evaluation.xlsx").is_file()
    assert "evaluation.xlsx" in (tmp_path / "download/report.html").read_text()
