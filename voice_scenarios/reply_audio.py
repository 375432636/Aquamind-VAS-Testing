"""Export sentence clips offline using packet order, never elapsed wall time."""

import wave
from pathlib import Path

from .reply_timing import analyze_reply_timing


def _read_pcm(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (
            1,
            2,
            "NONE",
        ):
            raise ValueError("unsupported_wav")
        pcm = audio.readframes(audio.getnframes())
        if len(pcm) != audio.getnframes() * 2:
            raise ValueError("truncated_wav")
        return audio.getframerate(), pcm


def _packet_index(turn):
    packets, ranges, position = {}, {}, 0
    for event in turn.get("events", []):
        if event["event"] != "audio_received":
            continue
        data = event.get("data", {})
        seq, size = data.get("audio_seq"), data.get("bytes")
        if (
            not isinstance(seq, int)
            or seq in packets
            or not isinstance(size, int)
            or size <= 0
            or size % 2
        ):
            raise ValueError("invalid_packet_index")
        packets[seq] = data
        ranges[seq] = (position, position + size)
        position += size
    return packets, ranges, position


def _source(turn, directory, kind, packets, received_ranges, received_bytes):
    filename = turn.get("audio", {}).get(kind)
    if not filename:
        raise ValueError("missing_wav")
    # Adjacent artifacts take priority when a report folder has been moved.
    path = directory / Path(filename).name
    if not path.is_file():
        path = Path(filename)
    rate, pcm = _read_pcm(path)
    if any(p.get("sample_rate", rate) != rate for p in packets.values()):
        raise ValueError("sample_rate_mismatch")
    if kind == "received":
        if received_bytes != len(pcm):
            raise ValueError("wav_packet_mismatch")
        return rate, pcm, received_ranges
    starts = [
        e["data"].get("audio_seq")
        for e in turn.get("events", [])
        if e["event"] == "playback_frame_started"
    ]
    if len(set(starts)) != len(starts) or any(seq not in packets for seq in starts):
        raise ValueError("invalid_playback_index")
    lengths = [packets[seq]["bytes"] for seq in starts]
    # ClockedPlayer writes full frames in playback order, except a cancelled
    # final frame. The actual WAV length determines that partial frame exactly.
    if not sum(lengths[:-1]) <= len(pcm) <= sum(lengths):
        raise ValueError("wav_playback_mismatch")
    ranges, position = {}, 0
    for seq, size in zip(starts, lengths):
        ranges[seq] = (position, min(position + size, len(pcm)))
        position += size
    return rate, pcm, ranges


def prepare_reply_audio(report, directory):
    """Attach portable clip links to report.json; preserve source artifacts."""
    directory = Path(directory)
    for index, turn in enumerate(report["turns"], 1):
        timing = analyze_reply_timing(turn)
        turn["reply_timing"] = timing
        try:
            packets, ranges, size = _packet_index(turn)
            index_error = None
        except ValueError as exc:
            packets, ranges, size = {}, {}, 0
            index_error = str(exc)
        for kind in ("received", "played"):
            try:
                if index_error:
                    raise ValueError(index_error)
                rate, pcm, positions = _source(
                    turn, directory, kind, packets, ranges, size
                )
                error = None
            except (OSError, EOFError, wave.Error, ValueError) as exc:
                error = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "missing_or_invalid_wav"
                )
            for sentence in timing["sentences"]:
                clip = {"status": "unavailable", "source": kind}
                sentence.setdefault("audio", {})[kind] = clip
                if error:
                    clip["reason"] = error
                    continue
                seqs = sentence["audio_seqs"]
                if any(seq not in packets for seq in seqs):
                    clip["reason"] = "missing_packet"
                    continue
                if any(
                    packets[seq].get("server_listen_turn_id")
                    not in (None, turn.get("listen_turn_id", index))
                    for seq in seqs
                ):
                    clip["reason"] = "cross_turn_audio"
                    continue
                chunks = [
                    pcm[a:b]
                    for seq in seqs
                    if seq in positions
                    for a, b in [positions[seq]]
                ]
                content = b"".join(chunks)
                if not content:
                    clip["reason"] = "not_played" if kind == "played" else "no_audio"
                    continue
                relative = (
                    Path("segments")
                    / f"turn-{index:03d}-segment-{sentence['index']:03d}.{kind}.wav"
                )
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".wav.tmp")
                with wave.open(str(temporary), "wb") as output:
                    output.setparams((1, 2, rate, 0, "NONE", ""))
                    output.writeframes(content)
                temporary.replace(target)
                clip.update(
                    status="ready",
                    path=relative.as_posix(),
                    samples=len(content) // 2,
                    duration_seconds=len(content) / (2 * rate),
                    complete=len(content) == sum(packets[s]["bytes"] for s in seqs),
                )
