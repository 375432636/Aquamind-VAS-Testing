from voice_scenarios.report import build_report, evaluate


def test_session_player_prefers_mixed_audio_and_retains_split_download(tmp_path):
    report = evaluate({"name": "完整对话", "status": "passed", "turns": []}, [])
    report["session_playback"] = {
        "status": "ready",
        "path": "session.played.wav",
        "playback_path": "session.mixed.wav",
        "duration_seconds": 2,
    }
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "report.html").read_text()
    assert 'src="session.mixed.wav"' in page
    assert 'href="session.mixed.wav" download' in page
    assert 'href="session.played.wav" download' in page
    assert page.count("<audio ") == 1


def test_vad_report_uses_speech_boundary_and_separate_server_endpoint_clock(tmp_path):
    result = {
        "name": "vad",
        "status": "passed",
        "turns": [
            {
                "id": "one",
                "status": "completed",
                "input_settings": {"mode": "vad"},
                "events": [
                    {
                        "event": "speech_input_finished",
                        "at_ns": 1_000_000_000,
                        "data": {},
                    },
                    {"event": "playback_started", "at_ns": 2_200_000_000, "data": {}},
                ],
            }
        ],
    }
    vas = [
        {
            "event": name,
            "listen_turn_id": 1,
            "monotonic_ns": int(at * 1e9),
            "clock_id": "vas",
            "data": {},
        }
        for name, at in [
            ("local_vad_speech_started", 1000),
            ("local_vad_last_voice", 1001),
            ("local_vad_endpoint_detected", 1001.5),
            ("asr_speech_started", 1000.1),
            ("asr_endpoint_detected", 1001.6),
            ("asr_final", 1001.7),
        ]
    ]
    report = evaluate(result, vas)
    metrics = report["turns"][0]["metrics"]
    assert metrics["first_playback_ms"] == 1200
    assert metrics["asr_commit_to_final_ms"] is None
    assert metrics["local_vad_silence_ms"] == 500
    assert metrics["asr_endpoint_to_final_ms"] == 100
    assert metrics["local_vad_starts"] == metrics["asr_vad_ends"] == 1
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "turn-001.html").read_text()
    assert "VAD" in page and "speech_input_finished" in page


def test_regression_uses_each_clock_and_does_not_sum_parallel_spans(tmp_path):
    result = {
        "name": "test",
        "status": "passed",
        "diagnostics": {"complete": True},
        "turns": [
            {
                "id": "t1",
                "listen_turn_id": 1,
                "status": "completed",
                "events": [
                    {"event": "listen_stop_sent", "at_ns": 1000000000, "data": {}},
                    {"event": "playback_started", "at_ns": 1200000000, "data": {}},
                ],
                "expected": {"llm_requests": 1, "max_first_playback_ms": 250},
                "interruption": None,
            }
        ],
    }
    vas = [
        {
            "event": "llm_request_started",
            "listen_turn_id": 1,
            "span_id": "s",
            "monotonic_ns": 900000000000,
            "clock_id": "server",
            "data": {},
        },
        {
            "event": "llm_request_finished",
            "listen_turn_id": 1,
            "span_id": "s",
            "monotonic_ns": 900010000000,
            "clock_id": "server",
            "data": {},
            "status": "ok",
        },
    ]
    report = evaluate(result, vas)
    assert report["status"] == "passed"
    assert report["turns"][0]["metrics"]["first_playback_ms"] == 200
    assert report["turns"][0]["spans"][0]["duration_ms"] == 10
    result["diagnostics"]["complete"] = False
    assert evaluate(result, vas)["status"] == "failed"
    build_report(report, tmp_path / "report.html")
    assert "900000000000" in (tmp_path / "turn-001.html").read_text()


def test_report_has_session_overview_and_portable_pages_for_each_turn(tmp_path):
    result = {
        "name": "连续上下文测试",
        "status": "passed",
        "diagnostics": {"complete": True},
        "run_metadata": {"environment": "dev"},
        "turns": [
            {
                "id": "one",
                "input_text": "推荐一款耳机",
                "status": "completed",
                "events": [],
            },
            {
                "id": "two",
                "input_text": "第一款多少钱？",
                "status": "completed",
                "events": [],
            },
        ],
    }
    report = evaluate(result, [])
    report["turns"][0]["reply_timing"] = {
        "sentences": [
            {"kind": "pre_speech", "start_seconds": 1, "end_seconds": 2},
            {"kind": "answer", "start_seconds": 5, "end_seconds": 6},
        ],
        "max_gap_seconds": 3,
    }
    build_report(report, tmp_path / "report.html")
    overview = (tmp_path / "report.html").read_text()
    first = (tmp_path / "turn-001.html").read_text()
    second = (tmp_path / "turn-002.html").read_text()
    assert overview == (tmp_path / "index.html").read_text()
    assert 'href="turn-001.html"' in overview
    assert 'href="turn-002.html"' in overview
    assert 'href="turn-002.html"' in first and "下一轮" in first
    assert 'href="turn-001.html"' in second and "上一轮" in second
    assert "推荐一款耳机" in first and "第一款多少钱？" in second
    assert "临时结束 → 正式开始" in first and "3.00" in first
    assert 'aria-current="page"' in first
    assert 'data-page="overview"' in overview
    assert 'data-page="turn"' in first
    assert "https://fonts" not in overview and "cdn" not in first


def test_report_keeps_capture_failures_visible_and_escapes_text(tmp_path):
    report = evaluate(
        {
            "name": "<script>bad()</script>",
            "status": "passed",
            "diagnostics": {"complete": False},
            "turns": [
                {
                    "id": "bad",
                    "input_text": '<img src=x onerror="bad()">',
                    "status": "failed",
                    "error": "连接已断开",
                    "events": [],
                }
            ],
        },
        [],
    )
    build_report(report, tmp_path / "report.html")
    overview = (tmp_path / "report.html").read_text()
    turn = (tmp_path / "turn-001.html").read_text()
    assert "诊断数据不完整" in overview and "诊断数据不完整" in turn
    assert "连接已断开" in turn
    assert "<script>bad()" not in overview
    assert "<img src=x" not in turn
    assert "&lt;img" in turn


def test_startup_failure_is_visible_even_without_turns(tmp_path):
    report = evaluate(
        {
            "name": "DEV connection",
            "status": "failed",
            "error": "WebSocket connection refused",
            "close_error": "diagnostic capture ended early",
            "turns": [],
        },
        [],
    )
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "report.html").read_text()
    assert "WebSocket connection refused" in page
    assert "diagnostic capture ended early" in page
    assert "需要检查" in page
    assert not (tmp_path / "turn-001.html").exists()


def test_disabled_diagnostics_and_untriggered_interrupt_are_not_successes(tmp_path):
    report = evaluate(
        {
            "name": "planned interrupt",
            "status": "failed",
            "run_metadata": {"diagnostics": "off"},
            "turns": [
                {
                    "id": "one",
                    "status": "failed",
                    "events": [],
                    "interruption": None,
                    "requested_interruption": {"after_playback_seconds": 2},
                }
            ],
        },
        [],
    )
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "turn-001.html").read_text()
    assert "打断未触发" in page
    assert "播放后 2.00 s" in page
    assert "LLM<b>未采集</b>" in page
    assert "触发误差</span><strong>— s" in page


def test_output_milestone_does_not_replace_tts_request_span():
    result = {
        "name": "tts",
        "status": "passed",
        "turns": [{"id": "turn", "status": "completed", "events": []}],
    }
    rows = [
        {
            "event": event,
            "span_id": "tts-1",
            "listen_turn_id": 1,
            "monotonic_ns": at_ns,
            "clock_id": "server",
            "data": {},
            "status": "ok",
        }
        for event, at_ns in [
            ("tts_request_started", 100000000),
            ("audio_output_started", 200000000),
            ("tts_request_finished", 900000000),
        ]
    ]
    rows[1]["data"]["audio_seq"] = 1
    report = evaluate(result, rows)
    assert [
        (span["name"], span["duration_ms"]) for span in report["turns"][0]["spans"]
    ] == [("tts_request", 800)]


def test_session_has_one_player_shared_clock_and_measurement_controls(tmp_path):
    report = evaluate(
        {
            "name": "整段会话",
            "status": "passed",
            "turns": [
                {
                    "id": "one",
                    "input_text": "你好",
                    "status": "completed",
                    "events": [],
                },
                {
                    "id": "two",
                    "input_text": "继续",
                    "status": "completed",
                    "events": [],
                },
            ],
        },
        [],
    )
    report["session_playback"] = {
        "status": "ready",
        "path": "session.played.wav",
        "duration_seconds": 18,
        "clock": "client_monotonic",
        "turns": [
            {"index": 1, "start_seconds": 0, "input_start_seconds": 0},
            {"index": 2, "start_seconds": 12, "input_start_seconds": 12},
        ],
        "segments": [
            {"turn_index": 1, "index": 1, "text": "仅属于第一轮的回复"},
            {"turn_index": 2, "index": 1, "text": "仅属于第二轮的回复"},
        ],
        "waits": [],
        "markers": [],
    }
    build_report(report, tmp_path / "report.html")
    overview = (tmp_path / "report.html").read_text()
    second = (tmp_path / "turn-002.html").read_text()
    assert overview.count("<audio ") == 1
    assert 'id="session-player"' in overview
    assert 'src="session.played.wav"' in overview
    assert 'id="session-timeline"' in overview
    assert 'id="measure-start"' in overview and 'id="measure-end"' in overview
    assert 'id="measure-duration"' in overview
    assert '"clock": "client_monotonic"' in overview
    assert "setPointerCapture" in overview and "pointerup" in overview
    assert second.count("<audio ") == 1
    assert 'id="session-timeline"' in second
    assert 'id="session-player"' in second
    assert 'src="session.played.wav"' in second
    assert 'href="report.html?t=12.000#session-timeline"' in second
    assert "输入结束 = 0 s" not in second
    assert "仅属于第一轮的回复" in overview
    assert "仅属于第二轮的回复" in second
    assert "仅属于第一轮的回复" not in second


def test_missing_session_audio_does_not_offer_empty_player(tmp_path):
    report = evaluate({"name": "empty", "status": "failed", "turns": []}, [])
    report["session_playback"] = {
        "status": "unavailable",
        "duration_seconds": 0,
        "limitations": [{"code": "missing_audio", "message": "未保存客户端音频"}],
    }
    build_report(report, tmp_path / "report.html")
    page = (tmp_path / "report.html").read_text()
    assert "<audio " not in page
    assert "未保存客户端音频" in page


def test_unadapted_tts_reports_unknown_metrics_without_false_cross_turn_failure():
    result = {
        "name": "legacy-provider",
        "status": "passed",
        "diagnostics": {"complete": True},
        "turns": [
            {
                "id": "turn",
                "status": "completed",
                "events": [
                    {
                        "event": "playback_frame_started",
                        "at_ns": 100,
                        "data": {"audio_seq": 1},
                    }
                ],
                "expected": {"tts_requests": 0},
            }
        ],
    }
    events = [
        {
            "event": "diagnostic_capabilities",
            "listen_turn_id": None,
            "data": {
                "component": "tts",
                "provider": "aliyun_stream",
                "request_timing": False,
                "output_attribution": False,
                "queue_timing": False,
            },
        },
        {
            "event": "audio_output_started",
            "listen_turn_id": None,
            "output_kind": "unknown",
            "output_id": "unattributed",
            "data": {"audio_seq": 1},
        },
    ]
    report = evaluate(result, events)
    metrics = report["turns"][0]["metrics"]
    assert metrics["tts_requests"] is None
    assert metrics["filler_outputs"] is None
    assert metrics["pre_speech_outputs"] is None
    assert report["metric_limitations"]
    assert report["turns"][0]["checks"][0]["passed"] is False
    assert len(report["failures"]) == 1
    assert "tts_requests" in report["failures"][0]


def test_offline_report_bundles_executable_timeline_dependencies(tmp_path):
    """Execute the generated bundle, not separately loaded source modules."""
    import re
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the generated offline report")
    report = evaluate({"name": "离线报告", "status": "passed", "turns": []}, [])
    page = tmp_path / "report.html"
    build_report(report, page)
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", page.read_text(), re.S)
    bundle = tmp_path / "bundle.js"
    bundle.write_text(
        "\n".join(body for attrs, body in scripts if "application/json" not in attrs)
    )
    validation = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const root={innerHTML:'',clientWidth:1000};
const context={document:{
  getElementById:id=>id==='data'?{textContent:'{"turn":null}'}:id==='chart'?root:null,
  addEventListener(){}
},window:{addEventListener(){}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8')+`
  timeline('chart',[{label:'VAS · ASR',segments:[{label:'ASR',start:0,end:1e9}],
    markers:[{label:'VAD 开始',role:'vad',start:0}]}],{},
    {embedded:true,start_ns:0,end_ns:2e9,detail:{},expandedLanes:new Set([0])});
  timelineSnap(.1,[{time:.1,label:'ASR'}],100);
  waitEvidence({turn_index:1,start_seconds:0,end_seconds:1},[]);
`,context);
assert.match(root.innerHTML,/data-event-index/);
assert.match(root.innerHTML,/VAD 开始/);
"""
    process = subprocess.run(
        [node, "-e", validation, str(bundle)], capture_output=True, text=True
    )
    assert process.returncode == 0, process.stderr


def test_manual_recording_report_distinguishes_stop_from_disabling_all_vad(tmp_path):
    result = {
        "name": "按住说话",
        "status": "passed",
        "turns": [
            {
                "id": "turn-001",
                "status": "completed",
                "input_type": "audio",
                "input_settings": {"mode": "manual"},
                "events": [{"event": "listen_stop_sent", "at_ns": 10, "data": {}}],
            }
        ],
    }
    rows = [
        {
            "event": name,
            "listen_turn_id": 1,
            "monotonic_ns": index,
            "clock_id": "vas",
            "data": data,
        }
        for index, (name, data) in enumerate(
            [
                ("asr_session_config_requested", {"vad_enabled": False}),
                ("asr_session_config_confirmed", {"vad_enabled": None}),
                ("local_vad_speech_started", {}),
                ("listen_stop_received", {}),
                ("asr_commit_sent", {}),
                ("asr_final", {}),
            ]
        )
    ]
    report = evaluate(result, rows)
    assert report["turns"][0]["input_control"]["local_vad"] == "observed"
    build_report(report, tmp_path / "report.html")
    for name in ["report.html", "turn-001.html"]:
        page = (tmp_path / name).read_text()
        assert "录音结束控制" in page
        assert "已发 1 次 / VAS 已收 1 次" in page
        assert "请求关闭 · 未明确回显" in page
        assert "仍在运行 · 1 个事件" in page
        assert "收到 stop → 提交 ASR → 最终结果" in page


def test_old_ptt_recording_gains_boundary_markers_in_overview_and_each_turn(tmp_path):
    import json
    import re

    report = evaluate(
        dict(
            name="PTT",
            status="passed",
            turns=[
                dict(
                    id=f"turn-{index}",
                    status="completed",
                    input_type="audio",
                    input_settings={"mode": "manual"},
                    events=[
                        dict(event="listen_start_sent", at_ns=index * 10**9, data={}),
                        dict(
                            event="listen_stop_sent",
                            at_ns=index * 10**9 + 500_123_000,
                            data={},
                        ),
                    ],
                )
                for index in (1, 2)
            ],
        ),
        [],
    )
    report["session_playback"] = dict(
        zero_at_ns=0,
        duration_seconds=3,
        status="ready",
        path="session.wav",
        turns=[dict(index=i, start_seconds=i, end_seconds=i + 0.6) for i in (1, 2)],
        markers=[
            dict(turn_index=i, kind="input_end", at_seconds=i + 0.500123)
            for i in (1, 2)
        ],
    )
    build_report(report, tmp_path / "report.html")
    for name, indices in [
        ("report.html", [1, 2]),
        ("turn-001.html", [1]),
        ("turn-002.html", [2]),
    ]:
        page = (tmp_path / name).read_text()
        data = json.loads(
            re.search(
                r'<script id="data" type="application/json">(.*?)</script>', page, re.S
            ).group(1)
        )
        markers = data["session_playback"]["markers"]
        assert markers == [
            m
            for i in indices
            for m in [
                dict(turn_index=i, kind="ptt_start", at_seconds=i),
                dict(
                    turn_index=i,
                    kind="ptt_stop",
                    at_seconds=(i * 10**9 + 500_123_000) / 1e9,
                ),
            ]
        ]
        assert "PTT 语音开始" in page and "PTT 语音结束" in page
    # Rendering old recordings must not alter their raw evidence.
    assert all(m["kind"] == "input_end" for m in report["session_playback"]["markers"])
