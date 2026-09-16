import asyncio
import json
import math
import struct
import wave

import opuslib_next as opuslib
import pytest

from voice_scenarios.websocket import WebSocketTransport


@pytest.mark.parametrize(
    "samples, packet_count", [(80, 1), (960, 2), (1100, 2), (1900, 3), (1920, 3)]
)
def test_manual_uplink_delivers_final_voice_samples_before_listen_stop(
    tmp_path, samples, packet_count
):
    """A final voice marker must survive the actual WAV → Opus → stop path."""
    source = tmp_path / "tail.wav"
    marker = [
        round(12000 * math.sin(i * 2 * math.pi * 1000 / 16000)) for i in range(80)
    ]
    pcm = struct.pack(f"<{samples}h", *([0] * (samples - len(marker)) + marker))
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(pcm)
    sent, events = [], []

    class Socket:
        async def send(self, packet):
            sent.append(packet)

    transport = WebSocketTransport("ws://unused", device_id="test")
    transport.ws = Socket()
    transport.emit = events.append
    metadata = asyncio.run(transport.send_audio(source))

    assert json.loads(sent[-1]) == {"type": "listen", "state": "stop"}
    decoder = opuslib.Decoder(16000, 1)
    received = b"".join(
        decoder.decode(packet, 960) for packet in sent if isinstance(packet, bytes)
    )
    values = struct.unpack(f"<{len(received) // 2}h", received)
    assert (
        max(abs(value) for value in values) > 3000
    ), "final voice marker was lost before listen/stop"
    assert len(values) == packet_count * 960
    assert metadata["frames"] == packet_count
    assert metadata["samples"] == samples
    # Codec padding is absent from the source WAV, so it must not become a
    # fictitious source frame in the report's recorded input timeline.
    source_frames = [
        event for event in events if event.kind == "input_audio_frame_sent"
    ]
    assert sum(event.data["samples"] for event in source_frames) == samples
    assert (
        source_frames[-1].data["pcm_offset_samples"] + source_frames[-1].data["samples"]
        == samples
    )
