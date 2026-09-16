"""Reconstruct a whole session on the recorded client monotonic clock.

Input packets occupy the left channel and consumed reply PCM the right channel
in the diagnostic export. A mono mix provides the default listening version so
both speakers remain audible on single-channel devices. Both WAVs retain pauses.
This is an offline reconstruction of ClockedPlayer, not a loudspeaker recording.
"""

import json
import sys
import wave
from array import array
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .input_control import refresh_ptt_markers
from .reply_audio import _packet_index
from .reply_timing import (
    analyze_reply_timing,
    input_end_event,
    ordered_playback_frames,
    playback_clock,
    validated_playback_frames,
)

SAMPLE_RATE = 16000
MAX_SESSION_SECONDS = 7200
MAX_TURNS = 30


@dataclass(frozen=True)
class _Source:
    path: Path
    samples: int
    rate: int


@dataclass(frozen=True)
class _Clip:
    source: _Source
    offset: int
    samples: int
    at_ns: int
    channel: int

    @property
    def end_ns(self):
        return self.at_ns + round(self.samples / self.source.rate * 1e9)


def _source(turn, directory, kind):
    filename = turn.get("audio", {}).get(kind)
    if not filename:
        raise ValueError(f"missing_{kind}_wav")
    path = directory / Path(filename).name
    if not path.is_file():
        path = Path(filename)
    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        if reader.getparams()[:2] != (1, 2) or rate not in (
            8000,
            12000,
            16000,
            24000,
            48000,
        ):
            raise ValueError("unsupported_audio_format")
        count = reader.getnframes()
        if count > MAX_SESSION_SECONDS * rate:
            raise ValueError("source_too_long")
        if count:
            reader.setpos(count - 1)
            if len(reader.readframes(1)) != 2:
                raise ValueError("truncated_wav")
    return _Source(path, count, rate)


def _time(events, kind):
    return next((e["at_ns"] for e in events if e["event"] == kind), None)


def _input_start(events):
    return next(
        (
            value
            for kind in (
                "text_sent",
                "sensor_sent",
                "speech_input_started",
                "first_audio_sent",
                "input_started",
                "input_audio_frame_sent",
                "input_speech_started",
            )
            if (value := _time(events, kind)) is not None
        ),
        None,
    )


def _input(turn, directory, frames, not_before):
    if turn.get("input_type") == "text":
        return [], [], "text", 0, 0
    if turn.get("sensor"):
        return [], [], "event", 0, 0
    events = turn.get("events", [])
    vad = turn.get("input_settings", {}).get("mode") == "vad"
    if frames:
        kind = "uplink" if vad else "input"
        source = _source(turn, directory, kind)
        if source.rate != SAMPLE_RATE:
            raise ValueError("unsupported_input_sample_rate")
        clips, spans, position = [], [], 0
        cursor, max_shift = not_before, 0
        for event in frames:
            data = event.get("data", {})
            count = data.get("samples")
            if (
                data.get("stream") != kind
                or data.get("sample_rate") != SAMPLE_RATE
                or data.get("pcm_offset_samples") != position
                or not isinstance(count, int)
                or count <= 0
                or position + count > source.samples
                or data.get("listen_turn_id") not in (None, turn.get("listen_turn_id"))
            ):
                raise ValueError("invalid_input_packet_index")
            sent = event["at_ns"]
            placed = max(sent, cursor)
            max_shift = max(max_shift, placed - sent)
            clip = _Clip(source, position, count, placed, 0)
            clips.append(clip)
            spans.append(
                (
                    clip.at_ns,
                    clip.end_ns,
                    bool(data.get("is_speech", True)),
                    sent,
                    sent + round(count / source.rate * 1e9),
                )
            )
            cursor = clip.end_ns
            position += count
        timing = (
            "recorded_with_overlap_adjustment"
            if max_shift > 1e9 / SAMPLE_RATE
            else "recorded"
        )
        return clips, spans, timing, source.samples - position, max_shift

    # Historical artifacts did not record each send. Keep their media duration,
    # but identify this placement as approximate rather than inventing pacing.
    kind = "uplink" if vad and turn.get("audio", {}).get("uplink") else "input"
    source = _source(turn, directory, kind)
    anchor = _time(events, "first_audio_sent")
    if anchor is None:
        anchor = _input_start(events)
    if vad and kind == "input":
        anchor = _time(events, "speech_input_started")
    if anchor is None:
        raise ValueError("missing_input_clock")
    actual_anchor = anchor
    anchor = max(anchor, not_before)
    shift = anchor - actual_anchor
    clip = _Clip(source, 0, source.samples, anchor, 0)
    speech_start = _time(events, "speech_input_started") if vad else anchor
    speech_end = _time(events, input_end_event(turn))
    spans = []
    if vad and kind == "uplink" and speech_start is not None and speech_end is not None:
        boundaries = [
            (anchor, min(clip.end_ns, speech_start), False),
            (max(anchor, speech_start), min(clip.end_ns, speech_end), True),
            (max(anchor, speech_end), clip.end_ns, False),
        ]
        spans = [span for span in boundaries if span[1] > span[0]]
    elif source.samples:
        spans = [(anchor, clip.end_ns, kind == "input")]
    spans = [
        (start, end, speech, start - shift, end - shift) for start, end, speech in spans
    ]
    return [clip] if source.samples else [], spans, "approximate", 0, shift


def _reply(turn, directory):
    clock = playback_clock(turn)
    if clock["status"] == "invalid":
        raise ValueError("invalid_playback_clock")
    if clock["status"] == "estimated":
        raise ValueError("estimated_playback_clock")
    starts = ordered_playback_frames(turn)
    if not starts:
        return [], {}
    source = _source(turn, directory, "played")
    packets, _, _ = _packet_index(turn)
    seqs = [e.get("data", {}).get("audio_seq") for e in starts]
    if (
        any(not isinstance(seq, int) or seq not in packets for seq in seqs)
        or len(set(seqs)) != len(seqs)
        or any(
            packets[seq].get("sample_rate", source.rate) != source.rate for seq in seqs
        )
        or any(
            packets[seq].get("server_listen_turn_id")
            not in (None, turn.get("server_listen_turn_id", turn.get("listen_turn_id")))
            for seq in seqs
        )
    ):
        raise ValueError("invalid_playback_index")
    sizes = [packets[seq]["bytes"] // 2 for seq in seqs]
    if not sum(sizes[:-1]) <= source.samples <= sum(sizes):
        raise ValueError("wav_playback_mismatch")
    clips, intervals, position = [], {}, 0
    previous_end = None
    for event, seq, size in list(zip(starts, seqs, sizes))[
        : clock["trusted_frame_count"]
    ]:
        count = min(size, source.samples - position)
        if (
            previous_end is not None
            and event["at_ns"] < previous_end - 1e9 / source.rate
        ):
            raise ValueError("overlapping_reply_playback")
        if count:
            clip = _Clip(source, position, count, event["at_ns"], 1)
            clips.append(clip)
            intervals[seq] = {
                "start_ns": clip.at_ns,
                "end_ns": clip.end_ns,
                "audio_seq": seq,
                "kind": packets[seq].get("output_kind", "unknown"),
                "complete": count == size,
            }
            previous_end = clip.end_ns
        position += size
    return clips, intervals


def _input_frames(turns, directory):
    """Route microphone tail events by source turn, including the durable journal."""
    rows = [event for turn in turns for event in turn.get("events", [])]
    journal = directory / "client-events.jsonl"
    if journal.is_file():
        with journal.open() as source:
            for line in source:
                row = json.loads(line)
                if row.get("event") == "input_audio_frame_sent":
                    rows.append(
                        {
                            "event": row["event"],
                            "at_ns": row["monotonic_ns"],
                            "data": row.get("data", {}),
                        }
                    )
    routed = {}
    for row in rows:
        if row.get("event") != "input_audio_frame_sent":
            continue
        data = row.get("data", {})
        turn_id, offset = data.get("listen_turn_id"), data.get("pcm_offset_samples")
        if not isinstance(turn_id, int) or not isinstance(offset, int):
            raise ValueError("invalid_input_packet_identity")
        identity = (turn_id, offset)
        if identity in routed and routed[identity] != row:
            # Result rows also have an offset_ms field; compare only wire data.
            old = routed[identity]
            if old["at_ns"] != row["at_ns"] or old.get("data") != data:
                raise ValueError("conflicting_input_packet_events")
        routed[identity] = row
    return {
        turn.get("listen_turn_id", index): [
            row
            for (owner, _), row in sorted(routed.items())
            if owner == turn.get("listen_turn_id", index)
        ]
        for index, turn in enumerate(turns, 1)
    }


def _read_output_samples(reader, clip, offset, count):
    """Resample a source slice to 16 kHz with bounded, linear interpolation."""
    rate = clip.source.rate
    first = offset * rate // SAMPLE_RATE
    last = min(clip.samples, ((offset + count - 1) * rate // SAMPLE_RATE) + 2)
    reader.setpos(clip.offset + first)
    content = reader.readframes(last - first)
    if len(content) != (last - first) * 2:
        raise ValueError("truncated_wav")
    values = array("h")
    values.frombytes(content)
    if sys.byteorder != "little":
        values.byteswap()
    if rate == SAMPLE_RATE:
        return values[:count]
    resampled = array("h")
    for position in range(offset, offset + count):
        base, remainder = divmod(position * rate, SAMPLE_RATE)
        base -= first
        a, b = values[base], values[min(base + 1, len(values) - 1)]
        resampled.append((a * (SAMPLE_RATE - remainder) + b * remainder) // SAMPLE_RATE)
    return resampled


def _write_session(directory, clips, zero, duration):
    """Write split and mixed versions in bounded blocks on the same clock."""
    target = directory / "session.played.wav"
    temporary = target.with_suffix(".wav.tmp")
    mixed_target = directory / "session.mixed.wav"
    mixed_temporary = mixed_target.with_suffix(".wav.tmp")
    count = round(duration * SAMPLE_RATE)
    positions = sorted(
        [
            (
                round((c.at_ns - zero) / 1e9 * SAMPLE_RATE),
                round(c.samples / c.source.rate * SAMPLE_RATE),
                c,
            )
            for c in clips
        ],
        key=lambda item: item[0],
    )
    with ExitStack() as stack:
        sources = {
            path: stack.enter_context(wave.open(str(path), "rb"))
            for path in {clip.source.path for clip in clips if clip.samples}
        }
        output = stack.enter_context(wave.open(str(temporary), "wb"))
        output.setparams((2, 2, SAMPLE_RATE, 0, "NONE", ""))
        mixed_output = stack.enter_context(wave.open(str(mixed_temporary), "wb"))
        mixed_output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", ""))
        active, cursor = [], 0
        for start in range(0, count, SAMPLE_RATE):
            end = min(count, start + SAMPLE_RATE)
            pcm = array("h", [0]) * ((end - start) * 2)
            active = [
                (at, size, clip) for at, size, clip in active if at + size > start
            ]
            while cursor < len(positions) and positions[cursor][0] < end:
                active.append(positions[cursor])
                cursor += 1
            for at, size, clip in active:
                left, right = max(start, at, 0), min(end, at + size)
                if left >= right:
                    continue
                values = _read_output_samples(
                    sources[clip.source.path], clip, left - at, right - left
                )
                offset = (left - start) * 2 + clip.channel
                pcm[offset : offset + len(values) * 2 : 2] = values
            # Preserve solo speech volume. Limit only overlapping peaks; the
            # diagnostic split export retains both original channel values.
            mixed = array(
                "h",
                (
                    max(-32768, min(32767, pcm[i] + pcm[i + 1]))
                    for i in range(0, len(pcm), 2)
                ),
            )
            if sys.byteorder != "little":
                pcm.byteswap()
                mixed.byteswap()
            output.writeframes(pcm.tobytes())
            mixed_output.writeframes(mixed.tobytes())
    temporary.replace(target)
    mixed_temporary.replace(mixed_target)
    return target.name, mixed_target.name


def prepare_session_playback(report, directory):
    """Enrich a report and export its continuous, portable client-clock replay."""
    directory = Path(directory)
    result = {
        "status": "unavailable",
        "sample_rate": SAMPLE_RATE,
        "channels": 2,
        "duration_seconds": 0,
        "zero_at_ns": None,
        "clock": "client_monotonic",
        "source": report.get("playback_source", "client_recorded_simulated_playback"),
        "limitations": [],
        "turns": [],
        "segments": [],
        "waits": [],
        "markers": [],
    }
    report["session_playback"] = result
    turns = report.get("turns", [])
    if len(turns) > MAX_TURNS:
        result["limitations"].append(
            {"code": "too_many_turns", "message": "会话超过 30 轮，未生成整段音频。"}
        )
        return result
    anchors = [
        e["at_ns"]
        for t in turns
        for e in t.get("events", [])
        if e["event"]
        in {
            "sensor_sent",
            "text_sent",
            "first_audio_sent",
            "input_started",
            "input_audio_frame_sent",
        }
    ]
    if report.get("connection_started_at_ns") is not None:
        anchors = [report["connection_started_at_ns"]]
    if not anchors:
        result["limitations"].append(
            {
                "code": "missing_session_clock",
                "message": "缺少客户端首帧发送时刻，无法对齐整段会话。",
            }
        )
        return result
    zero = result["zero_at_ns"] = min(anchors)

    def seconds(value):
        return (value - zero) / 1e9 if value is not None else None

    def limitation(code, index, detail):
        result["limitations"].append(
            {"code": code, "turn_index": index, "message": detail}
        )

    clips, incomplete, latest = [], False, zero
    startup = report.get("startup", {})
    if startup.get("received_frames"):
        try:
            greeting_clips, greeting_intervals = _reply(startup, directory)
            if playback_clock(startup)["status"] == "partial":
                incomplete = True
                limitation(
                    "partial_playback_clock",
                    0,
                    "欢迎语仅对齐浏览器输出时钟确认的连续前缀；未确认尾部未放入整段回听，原始完整音频仍保留。",
                )
                result["startup_unaligned_audio"] = {
                    kind: filename
                    for kind, filename in startup.get("audio", {}).items()
                    if kind in {"played", "received"}
                }
            clips.extend(greeting_clips)
            if greeting_clips:
                latest = max(latest, max(clip.end_ns for clip in greeting_clips))
                result["segments"].append(
                    {
                        "turn_index": 0,
                        "index": 0,
                        "kind": "greeting",
                        "status": startup.get("status"),
                        "text": " ".join(
                            e.get("data", {}).get("text", "")
                            for e in startup.get("events", [])
                            if e["event"] == "tts_sentence_start"
                        ),
                        "start_seconds": seconds(greeting_clips[0].at_ns),
                        "end_seconds": seconds(max(c.end_ns for c in greeting_clips)),
                        "audio_seqs": list(greeting_intervals),
                        "intervals": [
                            {
                                "start_seconds": seconds(c.at_ns),
                                "end_seconds": seconds(c.end_ns),
                            }
                            for c in greeting_clips
                        ],
                    }
                )
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            incomplete = True
            code = (
                str(exc)
                if str(exc) in {"invalid_playback_clock", "estimated_playback_clock"}
                else "greeting_audio_unavailable"
            )
            limitation(
                code, 0, f"首次问候音频无法按真实时刻对齐，原始音频仍可回听：{exc}"
            )
            result["startup_unaligned_audio"] = {
                kind: filename
                for kind, filename in startup.get("audio", {}).items()
                if kind in {"played", "received"}
            }
    input_cursor = zero
    input_error = None
    try:
        routed_inputs = _input_frames(turns, directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        routed_inputs, input_error = {}, exc
    for index, turn in enumerate(turns, 1):
        events = turn.get("events", [])
        clock = playback_clock(turn)
        endpoint = max(
            [zero, turn.get("ended_at_ns", zero)]
            + [
                e["at_ns"]
                for e in events
                if clock["status"] not in {"invalid", "estimated", "partial"}
                or e["event"] not in {"playback_started", "playback_frame_started"}
            ]
        )
        observed_endpoint = endpoint
        input_spans, input_clips, reply_clips, intervals = [], [], [], {}
        timing = "unavailable"
        try:
            if input_error:
                raise ValueError(f"invalid_input_event_journal: {input_error}")
            input_clips, input_spans, timing, untimed_samples, max_shift = _input(
                turn,
                directory,
                routed_inputs.get(turn.get("listen_turn_id", index), []),
                input_cursor,
            )
            input_cursor = max([input_cursor] + [clip.end_ns for clip in input_clips])
            if max_shift > 1e9 / SAMPLE_RATE:
                limitation(
                    "input_packet_overlap",
                    index,
                    f"输入发包间隔短于音频时长，仅回听音频按顺序排队保留全部采样，最大顺延 {max_shift / 1e9:.3f} 秒；时间轴仍使用原始客户端发送时刻。",
                )
                result["limitations"][-1]["max_shift_seconds"] = max_shift / 1e9
            if untimed_samples:
                incomplete = True
                limitation(
                    "input_samples_without_timing",
                    index,
                    f"已保存的输入有 {untimed_samples} 个采样点缺少逐帧时间，回听仅包含已确定时间的部分。",
                )
            if timing == "approximate":
                limitation(
                    "approximate_input_timing",
                    index,
                    "历史输入未记录逐帧发送时刻，输入音频按起点和素材时长近似摆放。",
                )
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            incomplete = True
            limitation(
                "input_audio_unavailable",
                index,
                f"输入音频无法准确重建：{type(exc).__name__}: {exc}",
            )
        reply_error = False
        try:
            reply_clips, intervals = _reply(turn, directory)
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            incomplete = reply_error = True
            limitation(
                (
                    str(exc)
                    if str(exc)
                    in {"invalid_playback_clock", "estimated_playback_clock"}
                    else "reply_audio_unavailable"
                ),
                index,
                (
                    "历史浏览器播放时钟无效，无法还原真实首音或对齐回复；等待区间仅表示首包等待，原始音频保留供回听。"
                    if str(exc) == "invalid_playback_clock"
                    else (
                        "浏览器仅记录了估计播放时刻，缺少可靠输出时钟；等待区间仅表示首包等待，原始音频保留供回听。"
                        if str(exc) == "estimated_playback_clock"
                        else f"回复音频无法准确重建：{type(exc).__name__}: {exc}；原始音频保留供回听。"
                    )
                ),
            )
        if clock["status"] == "partial":
            incomplete = True
            limitation(
                "partial_playback_clock",
                index,
                "已保留浏览器输出时钟确认的首音和连续播放前缀；未确认尾部未放入整段回听，原始完整回复可单独回听。",
            )
        clips.extend(input_clips + reply_clips)
        endpoint = max([endpoint] + [clip.end_ns for clip in input_clips + reply_clips])
        latest = max(latest, endpoint)
        input_start = _input_start(events)
        input_end = _time(events, input_end_event(turn))
        reply_events = [
            event
            for event in events
            if not event.get("data", {}).get("is_session_output")
        ]
        first_received = _time(reply_events, "audio_packet_received") or _time(
            reply_events, "audio_received"
        )
        valid_frames = validated_playback_frames(turn)
        first_playback = min(
            (
                e["at_ns"]
                for e in valid_frames
                if not e.get("data", {}).get("is_session_output")
            ),
            default=None,
        )
        row = {
            "index": index,
            "id": turn.get("id", str(index)),
            "text": turn.get("input_text")
            or turn.get("metrics", {}).get("asr_text")
            or "",
            "status": turn.get("status"),
            "start_seconds": max(
                0, seconds(turn.get("started_at_ns", input_start or zero))
            ),
            "input_start_seconds": seconds(input_start),
            "input_end_seconds": seconds(input_end),
            "first_received_seconds": seconds(first_received),
            "first_playback_seconds": seconds(first_playback),
            "playback_clock": clock,
            "end_seconds": seconds(endpoint),
            "input_segments": [],
        }
        if reply_error or clock["status"] == "partial":
            row["unaligned_audio"] = {
                kind: filename
                for kind, filename in turn.get("audio", {}).items()
                if kind in {"played", "received"}
            }
        for start, end, speech, sent_start, sent_end in input_spans:
            item = {
                "start_seconds": seconds(sent_start),
                "end_seconds": seconds(sent_end),
                "kind": "speech" if speech else "background",
                "timing": "approximate" if timing == "approximate" else "recorded",
                "sent_start_seconds": seconds(sent_start),
                "sent_end_seconds": seconds(sent_end),
                "replay_start_seconds": seconds(start),
                "replay_end_seconds": seconds(end),
                "replay_timing": timing,
            }
            previous = row["input_segments"][-1] if row["input_segments"] else None
            if (
                previous
                and previous["kind"] == item["kind"]
                and item["start_seconds"] <= previous["end_seconds"] + 1 / SAMPLE_RATE
            ):
                previous["end_seconds"] = max(
                    previous["end_seconds"], item["end_seconds"]
                )
                previous["sent_end_seconds"] = max(
                    previous["sent_end_seconds"], item["sent_end_seconds"]
                )
                previous["replay_end_seconds"] = max(
                    previous["replay_end_seconds"], item["replay_end_seconds"]
                )
            else:
                row["input_segments"].append(item)
        result["turns"].append(row)
        for kind, anchor in (
            ("input_started", input_start),
            ("sensor_sent" if turn.get("sensor") else "input_end", input_end),
            ("first_received", first_received),
            ("first_playback", first_playback),
            ("abort", _time(events, "abort_sent") or _time(events, "abort_requested")),
        ):
            if anchor is not None:
                result["markers"].append(
                    {"turn_index": index, "kind": kind, "at_seconds": seconds(anchor)}
                )
        sentences = analyze_reply_timing(turn)["sentences"]
        assigned = {seq for sentence in sentences for seq in sentence["audio_seqs"]}
        unassigned = [seq for seq in intervals if seq not in assigned]
        if unassigned:
            sentences.append(
                {
                    "index": len(sentences) + 1,
                    "text": "",
                    "kind": "unknown",
                    "audio_seqs": unassigned,
                    "status": "completed",
                }
            )
        spoken = []
        for sentence in sentences:
            frames = sorted(
                [intervals[seq] for seq in sentence["audio_seqs"] if seq in intervals],
                key=lambda frame: frame["start_ns"],
            )
            partial = any(not frame["complete"] for frame in frames)
            status = sentence["status"]
            if reply_error:
                status = "unavailable"
            elif partial:
                status = (
                    "interrupted"
                    if _time(events, "playback_stopped") is not None
                    else "incomplete"
                )
            segment = {
                "turn_index": index,
                "index": sentence["index"],
                "kind": sentence["kind"],
                "text": sentence["text"],
                "status": status,
                "audio_seqs": sentence["audio_seqs"],
                "start_seconds": seconds(frames[0]["start_ns"]) if frames else None,
                "end_seconds": (
                    seconds(max(frame["end_ns"] for frame in frames))
                    if frames
                    else None
                ),
                "intervals": [
                    {
                        "start_seconds": seconds(frame["start_ns"]),
                        "end_seconds": seconds(frame["end_ns"]),
                        "audio_seq": frame["audio_seq"],
                    }
                    for frame in frames
                ],
            }
            result["segments"].append(segment)
            if frames and sentence["kind"] != "greeting":
                spoken.append(segment)
        spoken.sort(key=lambda segment: segment["start_seconds"])
        waits = []
        if input_end is not None and (
            first_playback is None or first_playback >= input_end
        ):
            interruption = min(
                (
                    e["at_ns"]
                    for e in events
                    if e["event"]
                    in {"abort_sent", "abort_requested", "playback_stopped"}
                    and e["at_ns"] >= input_end
                ),
                default=None,
            )
            if first_playback is not None and first_playback >= input_end:
                kind, wait_end = "first_reply", first_playback
            elif first_received is not None and first_received >= input_end:
                kind, wait_end = "first_packet", first_received
            elif first_received is not None:
                # Audio was already received before release; there is no
                # post-release first-packet wait, even if playback is unknown.
                kind, wait_end = "first_packet", input_end
            else:
                kind, wait_end = "no_reply", observed_endpoint
            if interruption is not None and interruption < wait_end:
                kind, wait_end = "interrupted", interruption
            waits.append((kind, seconds(input_end), seconds(wait_end)))
        for previous, current in zip(spoken, spoken[1:]):
            kind = (
                "transition"
                if previous["kind"] in ("pre_speech", "filler")
                and current["kind"] == "answer"
                else "sentence"
            )
            waits.append((kind, previous["end_seconds"], current["start_seconds"]))
        result["waits"].extend(
            {
                "turn_index": index,
                "kind": kind,
                "start_seconds": start,
                "end_seconds": end,
                "duration_seconds": end - start,
            }
            for kind, start, end in waits
            if end > start
        )
    duration = seconds(latest)
    result["duration_seconds"] = duration
    refresh_ptt_markers(result, turns)
    if duration > MAX_SESSION_SECONDS:
        result["limitations"].append(
            {
                "code": "session_too_long",
                "message": "客户端时钟跨度超过 2 小时，未生成整段音频。",
            }
        )
        return result
    if not any(clip.samples and clip.end_ns > zero for clip in clips):
        return result
    try:
        result["path"], result["playback_path"] = _write_session(
            directory, clips, zero, duration
        )
        result["playback_channels"] = 1
        result["status"] = "incomplete" if incomplete else "ready"
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        result["limitations"].append(
            {
                "code": "session_export_failed",
                "message": f"整段音频导出失败：{type(exc).__name__}: {exc}",
            }
        )
    return result
