from copy import deepcopy

from voice_scenarios.replay_clock import reconstruct_simulated_playback


def sample():
    frames = [
        dict(
            event="audio_received",
            at_ns=i * 1000000,
            data=dict(audio_seq=i, bytes=1920, sample_rate=16000),
        )
        for i in (1, 2, 3)
    ]
    frames += [
        dict(
            event="playback_frame_started",
            at_ns=at,
            data=dict(audio_seq=i, source="simulated_player"),
        )
        for i, at in enumerate((1000000000, 1061000000, 1150000000), 1)
    ]
    return {"turns": [{"events": frames}]}, [
        dict(event="pcm", monotonic_ns=at, data={"audio_seq": i})
        for i, at in enumerate((990000000, 1050000000, 1140000000), 1)
    ]


def test_reconstruction_preserves_anchor_and_real_starvation_and_is_idempotent():
    result, journal = sample()
    assert reconstruct_simulated_playback(result, journal) == 3
    frames = [
        e
        for e in result["turns"][0]["events"]
        if e["event"] == "playback_frame_started"
    ]
    assert [e["at_ns"] for e in frames] == [1000000000, 1060000000, 1140000000]
    assert frames[1]["data"]["original_playback_at_ns"] == 1061000000
    before = deepcopy(result)
    assert reconstruct_simulated_playback(result, journal) == 0
    assert result == before


def test_missing_evidence_or_cancelled_or_browser_playback_is_not_reconstructed():
    for kind in ("missing", "cancelled", "browser"):
        result, journal = sample()
        if kind == "missing":
            journal.pop()
        if kind == "cancelled":
            result["turns"][0]["events"].append({"event": "playback_stopped"})
        if kind == "browser":
            result["turns"][0]["events"][-1]["data"]["source"] = "browser_audio_context"
        before = deepcopy(result)
        assert reconstruct_simulated_playback(result, journal) == 0
        assert result == before
