from copy import deepcopy

from voice_scenarios.tts_evidence import tts_segment_evidence


def test_plain_turn_after_chunk_turns_uses_explicit_server_to_client_mapping():
    from voice_scenarios.report import evaluate

    _, rows = request("a", "正式回答", [2, 3], turn=6, sequence=10)
    rows.append(server("tts_request_finished", "a", 20, turn=6))
    for row in rows:
        row["output_kind"] = "answer"
    cached = [
        server("tts_state_sent", None, 1, turn=6, state="sentence_start"),
        server("audio_output_frame", None, 2, turn=6, audio_seq=1),
    ]
    for row in cached:
        row.update(output_kind="filler", output_id="6:prepared:filler")
    original = deepcopy(cached + rows)
    turn = {
        "id": "three",
        "listen_turn_id": 3,
        "server_listen_turn_id": 6,
        "status": "completed",
        "events": sentence("嗯", [1], turn=3) + sentence("正式回答", [2, 3], turn=3),
    }
    result = evaluate(
        {"name": "混合输入", "status": "passed", "turns": [turn]}, cached + rows
    )
    actual = result["turns"][0]
    assert actual["reply_annotations"]["1"]["output_kind"] == "filler"
    assert actual["reply_annotations"]["2"]["output_kind"] == "answer"
    assert actual["spans"][0]["tts_evidence"]["text"] == "正式回答"
    assert actual["vas_events"] == original


def test_prepared_filler_without_request_does_not_hide_following_answer():
    from voice_scenarios.tts_evidence import cached_audio_annotations

    span, rows = request("a", "正式回答", [2, 3], sequence=10)
    cached = [
        server("tts_state_sent", None, 1, state="sentence_start"),
        server("audio_output_frame", None, 2, audio_seq=1),
    ]
    for event in cached:
        event.update(output_kind="filler", output_id="1:prepared:filler")
    client = sentence("嗯", [1]) + sentence("正式回答", [2, 3])
    evidence = tts_segment_evidence([span], cached + rows, client)
    assert evidence["a"]["text"] == "正式回答"
    assert (
        cached_audio_annotations(cached + rows, client)["1"]["output_kind"] == "filler"
    )
    cached[-1]["data"]["audio_seq"] = 42
    assert cached_audio_annotations(cached + rows, client) == {}
    assert (
        tts_segment_evidence([span], cached + rows, client)["a"]["status"]
        == "unmatched"
    )


def test_continuation_start_does_not_cut_off_playing_filler_packet_ownership():
    from voice_scenarios.tts_evidence import cached_audio_annotations

    cached = [
        server("tts_state_sent", None, 1, state="sentence_start"),
        server("audio_output_frame", None, 2, audio_seq=1),
        server("audio_output_frame", None, 3, audio_seq=2),
    ]
    for event in cached:
        event.update(output_kind="filler", output_id="1:prepared:filler")
    client = sentence("嗯", [1, 2])
    client.insert(2, dict(event="listen_start_sent", at_ns=201, data={}))
    client.append(dict(event="input_chunk_finished", at_ns=300, data={}))
    assert set(cached_audio_annotations(cached, client)) == {"1", "2"}


def server(event, span, seq, *, turn=1, session="session", **data):
    return dict(
        event=event,
        span_id=span,
        seq=seq,
        monotonic_ns=seq * 1_000_000,
        clock_id="vas",
        server_session_id=session,
        listen_turn_id=turn,
        output_id=f"{turn}:answer",
        data=data,
    )


def sentence(text, packets, *, turn=1, session="session", sentence_id="shared"):
    return [
        dict(
            event="tts_sentence_start",
            at_ns=100,
            data=dict(
                text=text,
                session_id=session,
                response_listen_turn_id=turn,
                sentence_id=sentence_id,
            ),
        ),
        *[
            dict(event="audio_packet_received", at_ns=200 + p, data={"audio_seq": p})
            for p in packets
        ],
    ]


def request(span, text, packets, *, turn=1, session="session", sequence=1):
    segment = f"segment-{span}"
    events = [
        server(
            "tts_segment_ready",
            "llm",
            sequence,
            turn=turn,
            session=session,
            segment_id=segment,
            text_chars=len(text),
            text_received_ns=5,
            trigger="punctuation",
        ),
        server(
            "tts_request_started",
            span,
            sequence + 1,
            turn=turn,
            session=session,
            segment_id=segment,
        ),
        server(
            "tts_state_sent",
            span,
            sequence + 2,
            turn=turn,
            session=session,
            state="sentence_start",
        ),
        *[
            server(
                "audio_output_frame",
                span,
                sequence + 3 + i,
                turn=turn,
                session=session,
                audio_seq=p,
            )
            for i, p in enumerate(packets)
        ],
    ]
    return dict(name="tts_request", span_id=span, data={"segment_id": segment}), events


def test_each_tts_segment_gets_its_own_text_despite_shared_sentence_id():
    a, ae = request("a", "第一句", [1, 2])
    b, be = request("b", "第二句更长", [3, 4], sequence=10)
    evidence = tts_segment_evidence(
        [a, b], ae + be, sentence("第一句", [1, 2]) + sentence("第二句更长", [3, 4])
    )
    assert evidence["a"]["text"] == "第一句"
    assert evidence["b"]["text"] == "第二句更长"
    assert evidence["a"]["source"] == "audio_sequence"
    assert evidence["a"]["source_label"] == "播报文本（按音频包关联）"
    assert evidence["a"]["segment_ready_ns"] == 1_000_000
    assert evidence["a"]["text_received_ns"] == 5
    assert "llm_sentence_ready_ns" not in evidence["a"]


def test_tool_rounds_and_reused_audio_sequences_cannot_cross_turn_or_session():
    a, ae = request("a", "过渡语", [1], turn=1)
    b, be = request("b", "正式回复", [1], turn=2)
    c, ce = request("c", "其他会话", [1], turn=1, session="other")
    events = (
        sentence("过渡语", [1], turn=1)
        + sentence("正式回复", [1], turn=2)
        + sentence("其他会话", [1], turn=1, session="other")
    )
    out = tts_segment_evidence([a, b, c], ae + be + ce, events)
    assert [out[k]["text"] for k in "abc"] == ["过渡语", "正式回复", "其他会话"]


def test_interrupt_sequence_drift_uses_complete_ordered_sentence_protocol():
    a, ae = request("a", "第一句", [7, 8])
    b, be = request("b", "第二句更长", [9, 10], sequence=10)
    out = tts_segment_evidence(
        [a, b], ae + be, sentence("第一句", [1, 2]) + sentence("第二句更长", [3, 4])
    )
    assert out["a"]["text"] == "第一句"
    assert out["b"]["text"] == "第二句更长"
    assert out["a"]["source"] == "sentence_protocol_order"
    assert out["a"]["source_label"] == "播报文本（按句消息顺序关联）"
    assert out["a"]["notes"]


def test_sequence_conflict_is_not_allowed_to_override_complete_protocol_order():
    a, ae = request("a", "甲句", [2])
    b, be = request("b", "乙句", [3], sequence=10)
    out = tts_segment_evidence(
        [a, b], ae + be, sentence("甲句", [1]) + sentence("乙句", [2])
    )
    assert out["a"]["text"] == "甲句"
    assert out["b"]["text"] == "乙句"
    assert out["b"]["source"] == "sentence_protocol_order"


def test_missing_sentence_cannot_shift_later_text_onto_another_request():
    a, ae = request("a", "甲句", [])
    b, be = request("b", "乙句", [], sequence=10)
    out = tts_segment_evidence([a, b], ae + be, sentence("乙句", []))
    assert out["a"]["status"] == "unmatched"
    assert out["b"]["status"] == "unmatched"


def test_equal_counts_with_wrong_character_length_do_not_establish_identity():
    a, ae = request("a", "甲句", [])
    b, be = request("b", "乙句更长", [], sequence=10)
    out = tts_segment_evidence(
        [a, b], ae + be, sentence("错误内容", []) + sentence("乙句更长", [])
    )
    assert all(e["status"] == "unmatched" for e in out.values())


def test_interrupted_unbroadcast_segment_retains_ready_time_but_no_text():
    a, ae = request("a", "未播出的句子", [])
    ae = [e for e in ae if e["event"] != "tts_state_sent"]
    out = tts_segment_evidence([a], ae, [])
    assert out["a"]["status"] == "unmatched"
    assert out["a"]["text"] is None
    assert out["a"]["segment_ready_ns"] == 1_000_000
    assert out["a"]["reason"]


def test_whole_output_text_is_never_assigned_to_multiple_request_spans():
    a, ae = request("a", "第一句", [1])
    b, be = request("b", "第二句", [2], sequence=10)
    out = tts_segment_evidence([a, b], ae + be, sentence("第一句第二句", [1, 2]))
    assert all(e["text"] is None for e in out.values())


def test_missing_session_or_turn_identity_is_not_inferred():
    a, ae = request("a", "第一句", [1])
    client = sentence("第一句", [1])
    client[0]["data"].pop("response_listen_turn_id")
    out = tts_segment_evidence([a], ae, client)
    assert out["a"]["status"] == "unmatched"


def test_startup_null_turn_can_be_matched_with_explicit_session_identity():
    a, ae = request("a", "欢迎光临", [1], turn=None)
    out = tts_segment_evidence([a], ae, sentence("欢迎光临", [1], turn=None))
    assert out["a"]["text"] == "欢迎光临"


def test_conflicting_request_identity_and_duplicate_packet_ownership_are_ambiguous():
    a, ae = request("a", "第一句", [1])
    b, be = request("b", "第二句", [1], sequence=10)
    events = [e for e in ae + be if e["event"] != "tts_state_sent"]
    out = tts_segment_evidence([a, b], events, sentence("第一句", [1]))
    assert all(e["status"] == "ambiguous" for e in out.values())


def test_helper_does_not_modify_source_evidence():
    a, ae = request("a", "第一句", [1])
    client = sentence("第一句", [1])
    originals = deepcopy(([a], ae, client))
    tts_segment_evidence([a], ae, client)
    assert ([a], ae, client) == originals


def test_packet_sequence_match_alone_cannot_bypass_missing_protocol_message():
    a, ae = request("a", "第一句", [1])
    b, be = request("b", "第二句", [2], sequence=10)
    out = tts_segment_evidence([a, b], ae + be, sentence("第二句", [1]))
    assert all(e["text"] is None for e in out.values())


def test_explicit_output_conflict_invalidates_order_mapping_for_the_group():
    a, ae = request("a", "第一句", [1])
    client = sentence("第一句", [1])
    client[0]["data"]["output_id"] = "another-llm:answer"
    out = tts_segment_evidence([a], ae, client)
    assert out["a"]["status"] == "unmatched"
    assert "输出标识" in out["a"]["reason"]


def test_duplicate_server_sentence_does_not_supply_fake_text_identity():
    a, ae = request("a", "第一句", [1])
    state = next(e for e in ae if e["event"] == "tts_state_sent")
    out = tts_segment_evidence([a], ae + [deepcopy(state)], sentence("第一句", [1]) * 2)
    assert out["a"]["status"] == "unmatched"
    assert out["a"]["text"] is None


def test_missing_ready_evidence_does_not_allow_text_length_to_be_guessed():
    a, ae = request("a", "第一句", [1])
    ae = [e for e in ae if e["event"] != "tts_segment_ready"]
    out = tts_segment_evidence([a], ae, sentence("第一句", [1]))
    assert out["a"]["status"] == "unmatched"
    assert "segment_ready_ns" not in out["a"]


def test_stage_diagnostics_without_frame_events_uses_sentence_protocol():
    a, ae = request("a", "第一句", [])
    out = tts_segment_evidence([a], ae, sentence("第一句", [1, 2]))
    assert out["a"]["status"] == "matched"
    assert out["a"]["source"] == "sentence_protocol_order"


def test_duplicate_span_id_from_other_session_is_not_accepted():
    a, ae = request("a", "第一句", [1])
    _, other = request("a", "其他话", [1], session="other")
    out = tts_segment_evidence([a], ae + other, sentence("第一句", [1]))
    assert out["a"]["status"] == "ambiguous"
    assert out["a"]["text"] is None
