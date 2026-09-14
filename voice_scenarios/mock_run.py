"""Deterministic offline recordings for exercising reports, not VAS evaluation.

No transport, remote model or remote TTS is instantiated. Recorded timestamps are
synthetic; local speech synthesis only makes the mock report replayable.
"""

import hashlib
import json
import random
import struct
import wave
from dataclasses import asdict
from pathlib import Path

from .__main__ import create_report
from .ci_run import synthesize
from .runner import save_result

# Independent recorded examples, deliberately not imported from the evaluator.
SAMPLE_TOOLS = {
    "知识库-文字": "rag-lightrag_search",
    "知识库-图文": "rag-lightrag_search",
    "知识库-视频": "rag-lightrag_search",
    "MCP-占星": "mock.unverified_astrology",
    "MCP-黄历": "Tung-Shing-get-tung-shing",
    "MCP-抽签": "DrawLots-drawLot",
    "MCP-天气": "get_weather",
    "MCP-新闻": "News-getTodayNewsByTopic",
    "MCP-音乐": "play_music",
    "Skill": "invoke_skill",
}
REPLIES = {
    "pre_speech": "这是模拟的临时回复，请稍等。",
    "answer": "这是模拟的正式回复，仅用于测试报告。",
}
BASE = 100_000_000_000
WALL = 1_789_459_200_000_000_000  # Fixed fixture clock, unrelated to execution time.
RATE = 16000


def execute_mock(output, settings, scenario):
    """Write raw client/server evidence and use the normal report/export pipeline."""
    output = Path(output)
    if any(turn.sensor or turn.interrupt for turn in scenario.turns):
        raise ValueError(
            "Mock recordings currently support sequential audio turns without interruption"
        )
    session_id = "mock-" + hashlib.sha256(scenario.name.encode()).hexdigest()[:12]
    sounds = {}
    for kind, text in REPLIES.items():
        path = output / f"mock-{kind}.wav"
        synthesize(text, path)
        with wave.open(str(path)) as source:
            pcm = source.readframes(source.getnframes())
        sounds[kind] = pcm
    result = {
        "schema_version": 1,
        "name": scenario.name,
        "status": "passed",
        "data_source": "mock",
        "run_metadata": {**settings, "data_source": "mock"},
        "client_clock": {"monotonic_ns": BASE, "wall_time_ns": WALL},
        "session": {"session_id": session_id},
        "diagnostics": {"complete": True},
        "evaluation": scenario.evaluation,
        "evaluation_turns": [
            dict(id=t.id, tool=t.tool, input_text=t.input_text) for t in scenario.turns
        ],
        "turns": [],
    }
    server, history = [], []
    cursor = 0.3
    sequence = 0
    for index, turn in enumerate(scenario.turns, 1):
        events = []

        def client(name, at, **data):
            events.append(
                {
                    "event": name,
                    "at_ns": BASE + round(at * 1e9),
                    "wall_time_ns": WALL + round(at * 1e9),
                    "data": data,
                }
            )

        def vas(name, at, span=None, parent=None, kind=None, **data):
            server.append(
                {
                    "schema_version": 1,
                    "source": "mock",
                    "clock_id": "mock-vas",
                    "server_session_id": session_id,
                    "listen_turn_id": index,
                    "span_id": span,
                    "parent_span_id": parent,
                    "output_kind": kind,
                    "event": name,
                    "monotonic_ns": BASE + round(at * 1e9),
                    "wall_time_ns": WALL + round(at * 1e9),
                    "status": "ok",
                    "data": data,
                }
            )

        with wave.open(str(turn.audio)) as source:
            input_pcm = source.readframes(source.getnframes())
        duration = len(input_pcm) / (RATE * 2)
        zero = cursor + duration
        client("first_audio_sent", cursor)
        if scenario.input.mode == "vad":
            client("speech_input_started", cursor)
        for offset in range(0, len(input_pcm) // 2, 960):
            client(
                "input_audio_frame_sent",
                cursor + offset / RATE,
                pcm_offset_samples=offset,
                samples=min(960, len(input_pcm) // 2 - offset),
                sample_rate=RATE,
                stream="uplink" if scenario.input.mode == "vad" else "input",
                listen_turn_id=index,
                is_speech=True,
            )
        client(
            (
                "speech_input_finished"
                if scenario.input.mode == "vad"
                else "listen_stop_sent"
            ),
            zero,
        )
        if scenario.input.mode == "vad":
            client("background_noise_started", zero)
            vas("local_vad_speech_started", cursor + 0.05)
            vas("local_vad_last_voice", zero)
            vas("local_vad_endpoint_detected", zero + 0.35)
            vas("asr_endpoint_detected", zero + 0.4)
        else:
            vas("listen_stop_received", zero + 0.04)
        vas(
            "asr_request_started",
            cursor,
            span=f"asr-{index}",
            provider="mock",
            model="fixture-asr",
        )
        vas("asr_commit_sent", zero + 0.4, span=f"asr-{index}")
        vas("asr_request_finished", zero + 0.55, span=f"asr-{index}")
        vas("asr_final", zero + 0.55, text=turn.input_text)
        client("stt", zero + 0.57, text=turn.input_text)
        vas("memory_request_started", zero + 0.56, span=f"memory-{index}")
        vas("memory_request_finished", zero + 0.65, span=f"memory-{index}")
        history.append({"role": "user", "content": turn.input_text})
        model = f"llm-{index}"
        vas(
            "llm_request_started",
            zero + 0.65,
            span=model,
            provider="mock",
            model="fixture-llm",
            llm_request_seq=1,
        )
        first = zero + 1 + (index % 4) * 0.25
        vas("llm_first_token", first - 0.3, span=model)
        tool_name = SAMPLE_TOOLS.get(turn.tool)
        kinds = ["pre_speech", "answer"] if tool_name else ["answer"]
        pcm_reply = bytearray()
        start = first
        for sentence, kind in enumerate(kinds, 1):
            pcm = sounds[kind]
            length = len(pcm) / (RATE * 2)
            request = f"tts-{index}-{sentence}"
            vas(
                "tts_request_started",
                start - 0.2,
                span=request,
                parent=model,
                kind=kind,
                provider="mock",
                model="fixture-tts",
                sentence_id=request,
                text=REPLIES[kind],
            )
            vas("tts_first_pcm", start - 0.05, span=request, parent=model, kind=kind)
            vas(
                "tts_request_finished",
                start + length,
                span=request,
                parent=model,
                kind=kind,
            )
            client("tts_sentence_start", start - 0.01, text=REPLIES[kind])
            if sentence == 1:
                client("playback_started", start)
            vas(
                "audio_output_started",
                start - 0.02,
                parent=request,
                kind=kind,
                audio_seq=sequence + 1,
                sentence_id=request,
            )
            for offset in range(0, len(pcm) // 2, 960):
                sequence += 1
                count = min(960, len(pcm) // 2 - offset)
                at = start + offset / RATE
                client("audio_packet_received", at - 0.005, audio_seq=sequence)
                client(
                    "audio_received",
                    at - 0.005,
                    audio_seq=sequence,
                    bytes=count * 2,
                    sample_rate=RATE,
                    duration_ms=count / RATE * 1000,
                    output_kind=kind,
                )
                client("playback_frame_started", at, audio_seq=sequence)
            pcm_reply.extend(pcm)
            client("tts_sentence_end", start + length)
            if kind == "pre_speech":
                gap = 0.5 + index % 3
                vas(
                    "tool_call_started",
                    start + 0.1,
                    span=f"tool-{index}",
                    parent=model,
                    tool_name=tool_name,
                    category=(
                        "knowledge_base"
                        if turn.tool.startswith("知识库")
                        else "business"
                    ),
                )
                vas(
                    "tool_call_finished",
                    start + length + gap - 0.25,
                    span=f"tool-{index}",
                    parent=model,
                    tool_name=tool_name,
                )
                start += length + gap
        end = start + length
        vas("llm_request_finished", start + 0.1, span=model)
        if turn.tool in {"知识库-图文", "知识库-视频"}:
            media = "image" if turn.tool == "知识库-图文" else "video"
            client(
                "display",
                start,
                items=[{"kind": media, "url": f"https://example.com/mock-{media}"}],
            )
        client("tts_stop", end)
        client("playback_drained", end)
        audio = {"input": str(turn.audio)}
        if scenario.input.mode == "vad":
            # Preserve the continuous uplink after speech, including background
            # noise while the simulated response is being played.
            count = round((end - zero) * RATE)
            noise = random.Random(scenario.input.noise_seed + index)
            amplitude = round(32767 * 10 ** (scenario.input.noise_dbfs / 20) * 3**0.5)
            pattern = struct.pack(
                "<960h", *(noise.randint(-amplitude, amplitude) for _ in range(960))
            )
            uplink = input_pcm + (pattern * ((count + 959) // 960))[: count * 2]
            for offset in range(0, count, 960):
                client(
                    "input_audio_frame_sent",
                    zero + offset / RATE,
                    pcm_offset_samples=len(input_pcm) // 2 + offset,
                    samples=min(960, count - offset),
                    sample_rate=RATE,
                    stream="uplink",
                    listen_turn_id=index,
                    is_speech=False,
                )
            path = output / f"turn-{index:03d}.uplink.wav"
            with wave.open(str(path), "wb") as target:
                target.setparams((1, 2, RATE, 0, "NONE", ""))
                target.writeframes(uplink)
            audio["uplink"] = str(path)
        for kind in ("played", "received"):
            path = output / f"turn-{index:03d}.{kind}.wav"
            with wave.open(str(path), "wb") as target:
                target.setparams((1, 2, RATE, 0, "NONE", ""))
                target.writeframes(pcm_reply)
            audio[kind] = str(path)
        result["turns"].append(
            {
                "id": turn.id,
                "listen_turn_id": index,
                "input_text": turn.input_text,
                "tool": turn.tool,
                "input_settings": asdict(scenario.input),
                "status": "completed",
                "expect": turn.expect,
                "events": sorted(events, key=lambda e: e["at_ns"]),
                "audio": audio,
                "started_at_ns": BASE + round(cursor * 1e9),
                "ended_at_ns": BASE + round(end * 1e9),
                "mock_history": list(history),
            }
        )
        history.append({"role": "assistant", "content": REPLIES["answer"]})
        cursor = end + 0.75
    save_result(result, output)
    (output / "vas-events.jsonl").write_text(
        "".join(
            json.dumps(event, ensure_ascii=False) + "\n"
            for event in sorted(server, key=lambda e: e["monotonic_ns"])
        )
    )
    report = create_report(output)
    return 0 if report["status"] == "passed" else 1
