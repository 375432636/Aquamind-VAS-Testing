import asyncio
import time
import wave

from .protocol import Event


class ClockedPlayer:
    """Silent PCM playback clock. It models buffering; it is not a speaker."""

    source = "simulated_player"

    def __init__(self, emit, path):
        self.emit = emit
        self.path = path
        self.queue = asyncio.Queue()
        self.sample_rate = None
        self.writer = None
        self.task = None
        self.finished = False
        self.started = False
        self.queued_seconds = 0.0

    def feed(self, pcm, sample_rate, metadata=None):
        if self.finished:
            raise ValueError("audio_received_after_tts_stop")
        if not pcm or len(pcm) % 2:
            raise ValueError("invalid_pcm")
        if self.sample_rate is not None and self.sample_rate != sample_rate:
            raise ValueError("audio_format_changed_within_turn")
        duration = len(pcm) / (sample_rate * 2)
        if self.queued_seconds + duration > 60:
            raise ValueError("playback_buffer_overflow")
        if self.writer is None:
            self.sample_rate = sample_rate
            self.writer = wave.open(str(self.path), "wb")
            self.writer.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            self.task = asyncio.create_task(self._play())
        self.queued_seconds += duration
        self.queue.put_nowait((pcm, metadata or {}))

    def finish(self):
        if not self.finished:
            self.finished = True
            self.queue.put_nowait(None)

    async def _play(self):
        while True:
            pcm = await self.queue.get()
            if pcm is None:
                self.emit(Event("playback_drained", {"source": self.source}))
                return
            pcm, metadata = pcm
            started_at = time.monotonic_ns()
            if metadata.get("audio_seq") is not None:
                self.emit(
                    Event(
                        "playback_frame_started",
                        {"source": self.source, **metadata},
                        started_at,
                    )
                )
            if not self.started:
                self.started = True
                self.emit(
                    Event("playback_started", {"source": self.source}, started_at)
                )
            duration = len(pcm) / (self.sample_rate * 2)
            try:
                await asyncio.sleep(duration)
            except asyncio.CancelledError:
                played = min(
                    len(pcm) // 2,
                    int((time.monotonic_ns() - started_at) / 1e9 * self.sample_rate),
                )
                self.writer.writeframes(pcm[: played * 2])
                raise
            self.writer.writeframes(pcm)
            self.queued_seconds -= duration

    async def close(self):
        await self.stop()
        if self.writer is not None:
            self.writer.close()

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
        self.finished = True
