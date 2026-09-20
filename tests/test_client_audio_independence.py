"""Client recordings survive unrelated server counters and local clock gaps."""

import copy
import struct
import wave

import pytest
from test_browser_timing import BASE, confirmed_data, event, response, turn
from test_tts_evidence import request, sentence, server

from voice_scenarios.evaluation import playback_metrics
from voice_scenarios.reply_audio import prepare_reply_audio
from voice_scenarios.reply_timing import packet_kind, playback_clock
from voice_scenarios.report import _timing_values, build_report, evaluate
from voice_scenarios.session_timing import prepare_session_playback


def recording(tmp_path):
    item = turn(tmp_path, response(3.25))
    item.update(status="completed", listen_turn_id=2)
    return dict(
        name="independent client",
        status="passed",
        turns=[item],
        connection_started_at_ns=BASE,
        client_clock=dict(monotonic_ns=BASE, wall_time_ns=BASE),
    )


def render(result, events, directory):
    report = evaluate(result, events)
    prepare_reply_audio(report, directory)
    playback = prepare_session_playback(report, directory)
    audio = (directory / playback["path"]).read_bytes()
    return report, playback, audio


def test_shifted_server_sequence_cannot_remove_or_retime_client_audio(tmp_path):
    raw = recording(tmp_path)
    original = copy.deepcopy(raw)
    expected, baseline, baseline_audio = render(raw, [], tmp_path)
    # Client seq 8 is turn 2, but the independent server counter begins at 10.
    server = [
        dict(
            event="audio_output_started",
            listen_turn_id=owner,
            monotonic_ns=seq,
            clock_id="vas",
            output_kind="answer",
            data=dict(audio_seq=seq),
        )
        for owner, seq in [(1, 1), (2, 10)]
    ]
    report, playback, audio = render(raw, server, tmp_path)
    assert report["turns"][0]["events"] == original["turns"][0]["events"]
    assert playback == baseline
    assert audio == baseline_audio
    assert not report["failure_groups"]["functional"]
    assert report["turns"][0]["metrics"]["first_playback_ms"] == 1250
    assert _timing_values(report["turns"][0])[0] == 1.25
    assert raw == original


def test_legacy_server_owner_never_erases_actual_played_audio(tmp_path):
    raw = recording(tmp_path)
    _, baseline, baseline_audio = render(raw, [], tmp_path)
    for e in raw["turns"][0]["events"]:
        if "audio_seq" in e.get("data", {}):
            e["data"]["server_listen_turn_id"] = 99
    report, playback, audio = render(raw, [], tmp_path)
    assert audio == baseline_audio
    assert playback["segments"] == baseline["segments"]
    assert (
        report["turns"][0]["reply_timing"]["sentences"][0]["audio"]["played"]["status"]
        == "ready"
    )


@pytest.mark.parametrize("kind", ["answer", "pre_speech", "filler"])
def test_verified_sentence_keeps_reply_type_without_mutating_client_packets(
    tmp_path, kind
):
    client = response(3.25)
    client[0]["data"] = sentence("真实回复", [8])[0]["data"]
    for e in client:
        e["data"].pop("output_kind", None)
    item = turn(tmp_path, client)
    original = copy.deepcopy(item)
    _, vas = request("tts-1", "真实回复", [9])
    vas.append(server("tts_request_finished", "tts-1", 9))
    for e in vas:
        e["output_kind"] = kind
    report = evaluate({"name": "reply type", "status": "passed", "turns": [item]}, vas)
    reported = report["turns"][0]
    assert reported["events"] == original["events"]
    assert packet_kind(reported, 8, {}) == kind
    assert reported["reply_annotations"]["8"]["output_id"] == "1:answer"
    assert reported["reply_annotations"]["8"]["source"] == "sentence_protocol_order"
    assert reported["metrics"]["first_playback_ms"] == 1250


def island_recording(tmp_path, uncertain_index=1):
    events = [event("tts_sentence_start", 3, text="完整回复")]
    for i in range(3):
        seq = 8 + i
        events.extend(
            [
                event("audio_packet_received", 3, audio_seq=seq),
                event(
                    "audio_received",
                    3,
                    audio_seq=seq,
                    bytes=3200,
                    sample_rate=16000,
                    duration_ms=100,
                    output_kind="answer",
                ),
                event(
                    "playback_frame_started",
                    3.25 + i * 0.1,
                    audio_seq=seq,
                    **confirmed_data(3.4 + i * 0.1, i != uncertain_index),
                ),
            ]
        )
    events.append(event("tts_stop", 4))
    item = turn(tmp_path, events)
    for kind in ("played", "received"):
        with wave.open(str(tmp_path / item["audio"][kind]), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", ""))
            output.writeframes(
                struct.pack("<4800h", *([1000] * 1600 + [2000] * 1600 + [3000] * 1600))
            )
    return dict(
        name="clock islands",
        status="passed",
        turns=[item],
        connection_started_at_ns=BASE,
    )


@pytest.mark.parametrize("uncertain_index", [0, 1])
def test_later_confirmed_intervals_keep_their_own_time_and_pcm_offset(
    tmp_path, uncertain_index
):
    raw = island_recording(tmp_path, uncertain_index)
    report, playback, _ = render(raw, [], tmp_path)
    clock = playback_clock(report["turns"][0])
    assert clock["trusted_frame_count"] == 2
    assert clock["unlocated_frame_count"] == 1
    intervals = playback["segments"][0]["intervals"]
    assert [x["audio_seq"] for x in intervals] == [
        8 + i for i in range(3) if i != uncertain_index
    ]
    assert intervals[-1]["start_seconds"] == pytest.approx(3.45)
    with wave.open(str(tmp_path / playback["path"])) as source:
        samples = struct.unpack(
            f"<{source.getnframes()*2}h", source.readframes(source.getnframes())
        )[1::2]
    assert samples[55200:56800] == (3000,) * 1600
    assert (1000 if uncertain_index == 0 else 2000) not in samples
    # A later known interval is not a replacement for an unknown first sound.
    assert playback["turns"][0]["first_playback_seconds"] == (
        None if uncertain_index == 0 else 3.25
    )
    assert report["turns"][0]["metrics"]["first_playback_ms"] == (
        None if uncertain_index == 0 else 1250
    )


def test_calibration_toggle_leaves_client_tracks_audio_and_metrics_unchanged(tmp_path):
    raw = recording(tmp_path)
    plain, playback, audio = render(raw, [], tmp_path)
    raw["clock_sync"] = dict(
        status="calibrated",
        offset_ns=120_000_000,
        uncertainty_ns=20_000_000,
        server_session_id="s",
        samples=[],
    )
    calibrated, other, other_audio = render(raw, [], tmp_path)
    assert other == playback and audio == other_audio
    assert calibrated["turns"][0]["metrics"] == plain["turns"][0]["metrics"]
    build_report(calibrated, tmp_path / "report.html")
    assert "跨端已校准" in (tmp_path / "report.html").read_text()


def test_unknown_first_frame_stays_unknown_in_excel_and_summary(tmp_path):
    report, _, _ = render(island_recording(tmp_path, 0), [], tmp_path)
    item = report["turns"][0]
    assert _timing_values(item)[0] is None
    assert playback_metrics(item)[0] == "未采集"


def test_unknown_sentence_tail_is_not_reported_as_transition_wait(tmp_path):
    raw = island_recording(tmp_path, 1)
    events = raw["turns"][0]["events"]
    for e in events:
        if e["event"] == "audio_received" and e["data"]["audio_seq"] in (8, 9):
            e["data"]["output_kind"] = "pre_speech"
    # The second sentence is confirmed, but the previous sentence's tail is not.
    pos = next(
        i
        for i, e in enumerate(events)
        if e["event"] == "audio_packet_received" and e["data"]["audio_seq"] == 10
    )
    events.insert(pos, event("tts_sentence_start", 3, text="正式回复"))
    report, playback, _ = render(raw, [], tmp_path)
    assert _timing_values(report["turns"][0])[2] is None
    assert not any(wait["kind"] == "transition" for wait in playback["waits"])


def test_diagnostic_missing_does_not_mislabel_an_interruption_as_completion(tmp_path):
    raw = recording(tmp_path)
    raw["turns"][0]["status"] = "interrupted"
    raw["diagnostics"] = dict(complete=False, finished=False)
    report, _, _ = render(raw, [], tmp_path)
    build_report(report, tmp_path / "report.html")
    html = (tmp_path / "report.html").read_text()
    assert "0 轮完成 · 1 轮打断 · 0 轮失败" in html
    assert 'aria-label="已打断"' in html
    assert (
        html.count("诊断数据不完整") == 1
    )  # One categorized explanation, no duplicate alert.
