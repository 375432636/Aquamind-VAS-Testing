import copy

from voice_scenarios.key_moments import add_key_moment_lanes
from voice_scenarios.timeline import group_timeline_spans

S = 10**9


def sample(mode="manual"):
    def marker(name, t, span=None, **data):
        return dict(
            event=name,
            start_ns=(100 + t) * S,
            plot_start_ns=t * S,
            clock_id="vas",
            span_id=span,
            data=data,
            label=name,
        )

    llm1 = [
        marker("llm_request_started", 5.5, "l1"),
        marker("llm_first_token", 6, "l1", delta_kind="tool"),
    ]
    llm2 = [
        marker("llm_request_started", 7, "l2"),
        marker("llm_first_token", 8, "l2", delta_kind="text"),
    ]

    def tts(sid, parent, start, pcm, kind, seq):
        return dict(
            category="tts_request",
            turn_index=1,
            llm_span_id=parent,
            segments=[
                dict(
                    span_id=sid,
                    clock_id="vas",
                    parent_span_id=parent,
                    output_kind=kind,
                    output_id=kind,
                    tts_evidence=dict(status="matched", audio_seqs=[seq]),
                )
            ],
            markers=[
                marker("tts_request_started", start, sid),
                marker("tts_first_pcm", pcm, sid),
            ],
        )

    lanes = [
        dict(
            category="asr_request",
            turn_index=1,
            segments=[],
            markers=[
                marker("local_vad_endpoint_detected", 4.2),
                marker("asr_endpoint_detected", 4),
                marker("asr_final", 5),
            ],
        ),
        dict(category="llm_request", turn_index=1, segments=[], markers=llm1),
        dict(category="llm_request", turn_index=1, segments=[], markers=llm2),
        tts("t1", "l1", 6.1, 6.3, "pre_speech", 1),
        tts("t2", "l2", 8.1, 8.4, "answer", 2),
    ]
    events = [
        dict(event="listen_start_sent", at_ns=S, data={"mode": mode}),
        dict(event="first_audio_sent", at_ns=2 * S, data={}),
        dict(event="listen_stop_sent", at_ns=4 * S, data={}),
        dict(event="speech_input_finished", at_ns=4 * S, data={}),
    ]
    events += [
        dict(event="playback_frame_started", at_ns=int(t * S), data={"audio_seq": seq})
        for seq, t in [(1, 6.5), (2, 8.6)]
    ]
    return dict(
        turns=[dict(events=events, playback_source="simulated_player")],
        session_playback=dict(
            zero_at_ns=0,
            markers=[dict(kind="first_playback", turn_index=1, at_seconds=6.5)],
            vas_timeline=dict(
                mode="wall", axis_start_seconds=0, axis_end_seconds=10, lanes=lanes
            ),
        ),
    )


def keys(report):
    add_key_moment_lanes(report)
    return [
        l
        for l in report["session_playback"]["vas_timeline"]["lanes"]
        if l["category"] == "key_moments"
    ]


def test_each_interval_has_its_own_row_and_real_endpoints():
    report = sample()
    original = copy.deepcopy(report["turns"])
    rows = keys(report)
    assert all(len(r["markers"]) <= 2 and len(r["intervals"]) <= 1 for r in rows)
    send = next(r for r in rows if r["key_id"] == "ptt")
    assert [p["plot_start_ns"] / S for p in send["markers"]] == [2, 4]
    assert send["intervals"][0]["duration_seconds"] == 2
    assert report["turns"] == original


def test_multiple_llm_calls_do_not_link_formal_text_to_earlier_pre_speech():
    rows = keys(sample())
    requests = [r for r in rows if r["key_id"] == "llm_first"]
    assert [r["llm_number"] for r in requests] == [1, 2]
    assert requests[0]["markers"][1]["label"] == "工具首包"
    assert requests[1]["markers"][1]["label"] == "文字首包"
    synthesis = [r for r in rows if r["key_id"] == "tts_first"]
    assert [r["llm_number"] for r in synthesis] == [1, 2]
    assert [
        (r["markers"][0]["span_id"], r["markers"][1]["span_id"]) for r in synthesis
    ] == [("l1", "t1"), ("l2", "t2")]
    assert [round(r["intervals"][0]["duration_seconds"], 1) for r in synthesis] == [
        0.3,
        0.4,
    ]
    played = [r for r in rows if r["key_id"] == "output_playback"]
    assert [r["markers"][1]["plot_start_ns"] / S for r in played] == [6.5, 8.6]


def test_tts_without_llm_identity_is_not_assigned_to_nearest_request():
    r = sample()
    tts = r["session_playback"]["vas_timeline"]["lanes"][-1]
    tts["llm_span_id"] = None
    tts["segments"][0]["parent_span_id"] = "chat-root"
    row = [row for row in keys(r) if row["key_id"] == "tts_first"][-1]
    assert row["llm_number"] is None
    assert "归属未采集" in row["label"]
    assert row["markers"][0]["span_id"] == "t2"


def test_vad_pairs_both_detectors_with_final_not_with_each_other():
    rows = keys(sample("auto"))
    vad = [r for r in rows if r["key_id"].startswith("vad_")]
    assert len(vad) == 2
    assert {r["markers"][0]["event"] for r in vad} == {
        "asr_endpoint_detected",
        "local_vad_endpoint_detected",
    }
    assert all(r["markers"][1]["event"] == "asr_final" for r in vad)
    assert all(r["intervals"][0]["duration_seconds"] > 0 for r in vad)


def test_missing_token_remains_unknown_without_using_other_request():
    r = sample()
    r["session_playback"]["vas_timeline"]["lanes"][2]["markers"].pop()
    row = [row for row in keys(r) if row["key_id"] == "llm_first"][-1]
    assert len(row["markers"]) == 1 and not row["intervals"]
    assert row["missing"]


def test_missing_tts_audio_keeps_the_request_visible_without_borrowing_other_output():
    r = sample()
    r["session_playback"]["vas_timeline"]["lanes"][-1]["markers"].pop()
    rows = keys(r)
    row = next(
        row for row in rows if row["key_id"] == "tts_first" and row["llm_number"] == 2
    )
    assert not row["intervals"]
    assert all(p["event"] != "tts_first_pcm" for p in row["markers"])
    assert "首音未采集" in row["missing"][0]


def test_relative_clocks_have_no_cross_domain_elapsed():
    r = sample()
    r["session_playback"]["vas_timeline"]["mode"] = "relative"
    rows = keys(r)
    cross = [
        i for row in rows for i in row["intervals"] if i["clock_basis"] == "unavailable"
    ]
    assert len(cross) == 3
    assert all(i["duration_seconds"] is None for i in cross)


def test_bounded_alignment_reports_cross_domain_uncertainty():
    r = sample()
    trace = r["session_playback"]["vas_timeline"]
    trace["mode"] = "bounded"
    trace["alignment"] = {"status": "bounded", "clock_id": "vas", "uncertainty_ms": 150}
    rows = keys(r)
    cross = [
        i
        for row in rows
        for i in row["intervals"]
        if i["clock_basis"] == "causal_bounded"
    ]
    assert len(cross) == 3
    assert all(i["uncertainty_seconds"] == 0.15 for i in cross)
    assert all("因果界限" in i["note"] for i in cross)


def test_browser_ptt_uses_first_recorded_frame_not_keydown():
    r = sample()
    r["turns"][0]["events"][1]["event"] = "input_audio_frame_sent"
    row = next(row for row in keys(r) if row["key_id"] == "ptt")
    assert row["markers"][0]["event"] == "input_audio_frame_sent"
    assert row["markers"][0]["plot_start_ns"] == 2 * S


def test_turn_isolation_and_repeat_render_does_not_duplicate():
    r = sample()
    first = copy.deepcopy(keys(r))
    assert keys(r) == first
    r["turns"].append(dict(events=[], playback_source="simulated_player"))
    other = [row for row in keys(r) if row["turn_index"] == 2]
    assert all(not row["markers"] for row in other)


def test_untrusted_playback_is_not_guessed_from_packet_receipt():
    r = sample()
    r["turns"][0]["events"] = [
        e for e in r["turns"][0]["events"] if e["event"] != "playback_frame_started"
    ]
    rows = keys(r)
    played = [row for row in rows if row["key_id"] == "output_playback"]
    assert all(len(row["markers"]) == 1 and row["missing"] for row in played)


def test_vad_keeps_separate_lane():
    events = [
        dict(event=name, monotonic_ns=i, span_id="asr", clock_id="vas", data={})
        for i, name in enumerate(
            [
                "asr_request_started",
                "local_vad_speech_started",
                "local_vad_endpoint_detected",
                "asr_endpoint_detected",
                "asr_final",
                "asr_request_finished",
            ]
        )
    ]
    spans = [
        dict(
            name="asr_request",
            span_id="asr",
            start_ns=0,
            end_ns=5,
            clock_id="vas",
            data={},
        )
    ]
    lanes = group_timeline_spans(spans, events)
    vad = next(l for l in lanes if l["category"] == "vad")
    assert {m["event"] for m in vad["markers"]} == {
        "local_vad_speech_started",
        "local_vad_endpoint_detected",
        "asr_endpoint_detected",
    }


def test_discarded_speculative_first_token_is_not_a_reply_keypoint():
    report = sample()
    report["turns"][0]["vas_events"] = [
        dict(
            event="llm_candidate_discarded",
            monotonic_ns=7 * S,
            data={"pipeline_attempt_id": "candidate-a"},
        ),
    ]
    lane = report["session_playback"]["vas_timeline"]["lanes"][1]
    lane["markers"][0]["data"]["pipeline_attempt_id"] = "candidate-a"
    rows = keys(report)
    assert not any(
        row["key_id"] == "llm_first" and row["llm_number"] == 1 for row in rows
    )
    assert any(row["key_id"] == "llm_first" and row["llm_number"] == 2 for row in rows)
