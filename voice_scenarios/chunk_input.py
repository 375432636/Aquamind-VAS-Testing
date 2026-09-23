"""Paced multi-chunk speech with one client turn and explicit endpoint gaps."""

import asyncio
import math
import random
import struct
import time
import wave

import opuslib_next as opuslib

from .clock_sync import ClockSyncSamples
from .protocol import Event


async def stream_chunks(transport, chunks, settings, output, completed):
    sources = []
    for chunk in chunks:
        with wave.open(str(chunk.audio), "rb") as audio:
            if (
                audio.getnchannels(),
                audio.getsampwidth(),
                audio.getframerate(),
                audio.getcomptype(),
            ) != (1, 2, 16000, "NONE"):
                raise ValueError("chunk must be mono PCM16 WAV at 16000 Hz")
            pcm = audio.readframes(audio.getnframes())
            if not pcm:
                raise ValueError("empty audio chunk")
            sources.append(pcm)
    vad = settings.mode == "vad"
    if vad and transport.diagnostics == "off":
        raise ValueError(
            "VAD continuation tests require diagnostics to observe the endpoint"
        )
    encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    rng = random.Random(settings.noise_seed)
    amplitude = 32767 * 10 ** (settings.noise_dbfs / 20) * math.sqrt(3)
    ids, total = [], 0
    started = time.monotonic()
    turn_id = transport.turn_sequence
    with wave.open(str(output), "wb") as saved:
        saved.setparams((1, 2, 16000, 0, "NONE", ""))

        async def frame(raw=b"", *, speech=False, upload=True):
            nonlocal total
            await asyncio.sleep(max(0, started + total * 0.06 - time.monotonic()))
            values = struct.unpack("<960h", raw.ljust(1920, b"\0"))
            pcm = struct.pack(
                "<960h",
                *(
                    (
                        max(
                            -32768,
                            min(
                                32767, value + round(rng.uniform(-amplitude, amplitude))
                            ),
                        )
                        if vad
                        else value
                    )
                    for value in values
                ),
            )
            if upload:
                await transport.ws.send(encoder.encode(pcm, 960))
            at_ns = time.monotonic_ns()
            saved.writeframesraw(pcm)
            if upload:
                transport.emit(
                    Event(
                        "input_audio_frame_sent",
                        dict(
                            pcm_offset_samples=total * 960,
                            samples=960,
                            sample_rate=16000,
                            stream="uplink",
                            is_speech=speech,
                            listen_turn_id=turn_id,
                        ),
                        at_ns,
                    )
                )
            if total == 0:
                transport.emit(
                    Event("first_audio_sent", {"listen_turn_id": turn_id}, at_ns)
                )
            total += 1

        async def start_listen():
            transport.listen_sequence += 1
            ids.append(transport.listen_sequence)
            transport.response_turns[transport.listen_sequence] = turn_id
            await transport._send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto" if vad else "manual",
                }
            )
            transport.emit(
                Event(
                    "listen_start_sent",
                    dict(
                        listen_turn_id=turn_id,
                        server_listen_turn_id=transport.listen_sequence,
                        mode="auto" if vad else "manual",
                    ),
                )
            )

        if vad:
            await start_listen()
            for _ in range(math.ceil(settings.pre_roll_seconds / 0.06)):
                await frame()
        previous_endpoint = None
        for number, (chunk, pcm) in enumerate(zip(chunks, sources), 1):
            if number > 1:
                if vad:
                    limit = time.monotonic() + 5
                    endpoint = None
                    while time.monotonic() < limit:
                        while not transport.speech_endpoints.empty():
                            row, received = transport.speech_endpoints.get_nowait()
                            if row.get("listen_turn_id") in ids:
                                endpoint = (row, received)
                        if endpoint is not None:
                            break
                        await frame()
                    if endpoint is None:
                        raise TimeoutError("VAS speech endpoint was not observed")
                    row, received = endpoint
                    calibration = ClockSyncSamples(transport.clock_samples).summary()
                    anchor = transport.clock_anchor
                    if (
                        calibration["status"] == "calibrated"
                        and anchor
                        and row.get("wall_time_ns")
                    ):
                        previous_endpoint = (
                            row["wall_time_ns"]
                            - calibration["offset_ns"]
                            - anchor["wall_time_ns"]
                            + anchor["monotonic_ns"]
                        ) / 1e9
                    else:
                        previous_endpoint = received / 1e9
                        transport.emit(
                            Event(
                                "chunk_gap_uncalibrated",
                                {
                                    "message": "续说间隔使用端点到达时间；服务器实际间隔以诊断事件为准"
                                },
                            )
                        )
                target = previous_endpoint + chunk.resume_after_endpoint_ms / 1000
                while started + total * 0.06 < target:
                    await frame(upload=vad)
                transport.emit(
                    Event(
                        "chunk_resume_scheduled",
                        dict(
                            chunk_id=number,
                            target_at_ns=int(target * 1e9),
                            lateness_seconds=max(0, time.monotonic() - target),
                        ),
                    )
                )
            if not vad:
                await start_listen()
            transport.emit(
                Event(
                    "input_chunk_started",
                    {"chunk_id": number, "listen_turn_id": turn_id},
                )
            )
            for offset in range(0, len(pcm), 1920):
                await frame(pcm[offset : offset + 1920], speech=True)
            # Drain encoder lookahead without truncating the final syllable.
            await frame()
            await asyncio.sleep(max(0, started + total * 0.06 - time.monotonic()))
            transport.emit(
                Event(
                    "input_chunk_finished",
                    {"chunk_id": number, "listen_turn_id": turn_id},
                )
            )
            if not vad:
                await transport._send_json({"type": "listen", "state": "stop"})
                previous_endpoint = time.monotonic()
                transport.emit(
                    Event(
                        "listen_stop_sent",
                        dict(
                            listen_turn_id=turn_id,
                            server_listen_turn_id=transport.listen_sequence,
                            chunk_id=number,
                        ),
                    )
                )
        metadata = dict(
            frames=total,
            samples=sum(len(pcm) // 2 for pcm in sources),
            listen_turn_id=turn_id,
            server_listen_turn_id=ids[-1],
            server_listen_turn_ids=ids,
        )
        transport.emit(
            Event("speech_input_finished" if vad else "audio_send_completed", metadata)
        )
        completed.set_result(metadata)
        if vad:
            while True:
                await frame()
