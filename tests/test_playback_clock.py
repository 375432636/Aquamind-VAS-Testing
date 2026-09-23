import asyncio
import wave

from voice_scenarios.player import ClockedPlayer
from voice_scenarios.session_timing import _reply, _write_session


def test_buffered_pcm_stays_contiguous_through_scheduler_jitter(tmp_path, monkeypatch):
    import voice_scenarios.player as module

    now = [10_000_000_000]
    real_sleep = asyncio.sleep

    async def delayed_sleep(seconds):
        now[0] += round(seconds * 1e9) + 1_000_000
        await real_sleep(0)

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", delayed_sleep)
    emitted = []
    path = tmp_path / "played.wav"

    async def run():
        player = ClockedPlayer(emitted.append, path)
        for seq in (1, 2):
            player.feed(b"\x10\x10" * 960, 16000, {"audio_seq": seq})
        player.finish()
        await player.task
        await player.close()

    asyncio.run(run())
    starts = [e for e in emitted if e.kind == "playback_frame_started"]
    assert starts[1].at_ns - starts[0].at_ns == 60_000_000
    turn = {
        "audio": {"played": str(path)},
        "events": [
            {
                "event": "audio_received",
                "at_ns": 10_000_000_000,
                "data": {"audio_seq": seq, "bytes": 1920, "sample_rate": 16000},
            }
            for seq in (1, 2)
        ]
        + [{"event": e.kind, "at_ns": e.at_ns, "data": e.data} for e in starts],
    }
    clips, _ = _reply(turn, tmp_path)
    _, mixed = _write_session(tmp_path, clips, starts[0].at_ns, 0.12)
    with wave.open(str(tmp_path / mixed)) as source:
        assert source.readframes(source.getnframes()) == b"\x10\x10" * 1920


def test_late_pcm_retains_real_starvation(tmp_path, monkeypatch):
    import voice_scenarios.player as module

    now = [10_000_000_000]
    real_sleep = asyncio.sleep
    events = []

    async def sleep(seconds):
        now[0] += round(seconds * 1e9)
        await real_sleep(0)

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    async def run():
        player = ClockedPlayer(events.append, tmp_path / "played.wav")
        player.feed(b"\x10\x10" * 960, 16000, {"audio_seq": 1})
        await real_sleep(0)
        await real_sleep(0)
        now[0] += 20_000_000
        player.feed(b"\x10\x10" * 960, 16000, {"audio_seq": 2})
        player.finish()
        await player.task
        await player.close()

    asyncio.run(run())
    starts = [e.at_ns for e in events if e.kind == "playback_frame_started"]
    assert starts[1] - starts[0] == 80_000_000
