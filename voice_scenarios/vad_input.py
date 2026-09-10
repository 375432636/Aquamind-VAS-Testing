"""A paced microphone: source speech mixed with reproducible, continuous noise."""

import asyncio
import math
import random
import struct
import time
import wave

import opuslib_next as opuslib

from .protocol import Event


async def stream_microphone(ws, path, settings, turn_id, emit, speech_done, output):
    """Continue beyond the speech future until the owner cancels the microphone.

    The saved uplink is PCM before Opus encoding, including pre-roll and tail.
    Cancellation ends the test input stream; it never sends a listen/stop.
    """
    rng = random.Random(settings.noise_seed)
    amplitude = 32767 * 10 ** (settings.noise_dbfs / 20) * math.sqrt(3)
    encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    with wave.open(str(path), "rb") as source, wave.open(str(output), "wb") as saved:
        if (
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
            source.getcomptype(),
        ) != (1, 2, 16000, "NONE") or not source.getnframes():
            raise ValueError("input must be nonempty mono PCM16 WAV at 16000 Hz")
        saved.setparams((1, 2, 16000, 0, "NONE", ""))
        samples = source.getnframes()
        pre_frames = math.ceil(settings.pre_roll_seconds / 0.06)
        speech_frames = math.ceil(samples / 960)
        total = 0
        started = time.monotonic()
        speech_end = started + pre_frames * 0.06 + samples / 16000
        while True:
            due = started + total * 0.06
            if not speech_done.done() and due >= speech_end:
                await asyncio.sleep(max(0, speech_end - time.monotonic()))
                metadata = {
                    "samples": samples,
                    "frames": speech_frames,
                    "listen_turn_id": turn_id,
                }
                emit(Event("speech_input_finished", metadata))
                emit(
                    Event(
                        "background_noise_started",
                        {"noise_dbfs": settings.noise_dbfs, "listen_turn_id": turn_id},
                    )
                )
                speech_done.set_result(metadata)
            await asyncio.sleep(max(0, due - time.monotonic()))
            is_speech = pre_frames <= total < pre_frames + speech_frames
            if total == pre_frames:
                emit(Event("speech_input_started", {"listen_turn_id": turn_id}))
            raw = (
                source.readframes(960).ljust(1920, b"\0") if is_speech else b"\0" * 1920
            )
            values = struct.unpack("<960h", raw)
            pcm = struct.pack(
                "<960h",
                *(
                    max(
                        -32768,
                        min(32767, value + round(rng.uniform(-amplitude, amplitude))),
                    )
                    for value in values
                ),
            )
            await ws.send(encoder.encode(pcm, 960))
            sent_at = time.monotonic_ns()
            saved.writeframesraw(pcm)
            emit(
                Event(
                    "input_audio_frame_sent",
                    {
                        "pcm_offset_samples": total * 960,
                        "samples": 960,
                        "sample_rate": 16000,
                        "stream": "uplink",
                        "is_speech": is_speech,
                        "listen_turn_id": turn_id,
                    },
                    sent_at,
                )
            )
            if total == 0:
                emit(Event("first_audio_sent", {"listen_turn_id": turn_id}, sent_at))
            total += 1
