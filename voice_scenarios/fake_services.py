"""Loopback-only scripted HTTP/SSE/Realtime providers. No remote dependencies."""

import asyncio
import base64
import json
import logging
import math
import struct
import time
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

LOG = logging.getLogger(__name__)


def create_app(script=None, pcm_fixture=None):
    script = script or {
        "turns": [
            {"text": "你好，请介绍一下自己。", "reply": "你好，我是本地测试助手。"}
        ]
    }
    app = web.Application(client_max_size=8 * 1024 * 1024)
    state = SimpleNamespace(records=[], asr_index=0)
    if pcm_fixture:
        with wave.open(str(pcm_fixture)) as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (
                24000,
                1,
                2,
            ):
                raise ValueError("TTS fixture must be mono PCM16 24000 Hz")
            pcm = audio.readframes(audio.getnframes())
    else:
        # Unit-test fallback is labelled silence; development stack supplies speech.
        pcm = b"\0\0" * 24000
    state.pcm = pcm

    def record(service, **data):
        row = {"service": service, "monotonic_ns": time.monotonic_ns(), **data}
        state.records.append(row)
        if len(state.records) > 10000:
            del state.records[:5000]
        LOG.info("%s", json.dumps(row, ensure_ascii=False))

    async def health(request):
        return web.json_response({"status": "ok", "fake": True})

    async def records(request):
        return web.json_response(state.records)

    async def ums(request):
        tail = request.match_info["name"]
        record("ums", operation=tail)
        if tail == "token":
            body = await request.json()
            state.asr_index = 0
            data = {
                "access_token": "local-" + body["device_fingerprint"],
                "token_type": "bearer",
            }
        elif tail == "apikey":
            data = {"api_key": "sk-fake-local-session"}
        elif tail == "prompt":
            data = {
                "prompt": {
                    "enable_langfuse": False,
                    "enable_voiceid": False,
                    "enable_safety_filter": bool(script.get("guardrail")),
                    "guardrail": script.get("guardrail", {}),
                    "max_turns": 20,
                    "first_greeting": "",
                    "filler_words": ["请稍等。"],
                    "models": {
                        "chatAgentProvider": "AliLLM",
                        "chatAgent": "fake-chat",
                        "chatEnableThinking": False,
                        "intentAgentProvider": "AliLLM",
                        "intentAgent": "fake-intent",
                        "asrProvider": "DashScopeStreamASR",
                        "ttsProvider": "MinimaxTTSHTTPStream",
                        "ttsModel": "fake-speech",
                        "ttsSpeaker": "fixture",
                        "ttsSpeed": 1,
                        "ttsVolume": 1,
                    },
                    "prompts": {
                        "base_prompt": "你是本地测试助手，按工具协议回复。",
                        "agent_prompt": "{{base_prompt}}",
                    },
                }
            }
        else:
            return web.json_response({"error": "unknown fake UMS endpoint"}, status=404)
        return web.json_response({"code": 200, "data": data})

    async def memory(request):
        body = await request.json()
        record(
            "memory",
            operation=request.match_info["name"],
            query=body.get("query"),
            session_id=(body.get("session") or {}).get("session_id"),
        )
        await asyncio.sleep(script.get("memory_delay_ms", 80) / 1000)
        return web.json_response(
            {
                "status": "success",
                "request_id": uuid.uuid4().hex,
                "entities": [],
                "conversations": [],
                "observations": [],
                "stage": "complete",
            }
        )

    async def asr(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        index = state.asr_index
        state.asr_index += 1
        # A development stack accepts one scripted conversation at a time;
        # separate stacks / ports provide deterministic concurrent scenarios.
        turn = script["turns"][index % len(script["turns"])]
        size = 0
        auto_vad = False
        speech_active = False
        quiet_samples = 0
        completed = False

        async def finish_transcript():
            nonlocal completed
            completed = True
            await asyncio.sleep(turn.get("asr_final_delay_ms", 90) / 1000)
            await ws.send_json(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": turn["text"],
                    "language": "zh",
                    "item_id": uuid.uuid4().hex,
                }
            )

        await ws.send_json(
            {"type": "session.created", "session": {"id": uuid.uuid4().hex}}
        )
        record("asr", operation="connected", turn=index + 1)
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            body = json.loads(msg.data)
            kind = body.get("type")
            if kind == "input_audio_buffer.append":
                frame = base64.b64decode(body["audio"], validate=True)
                if not frame or len(frame) % 2:
                    await ws.close(code=1008, message=b"invalid PCM")
                    break
                size += len(frame)
                if auto_vad and not completed:
                    # Deliberately simple Fake endpoint detector. It consumes PCM;
                    # elapsed wall time / absence of packets cannot end speech.
                    values = struct.unpack(f"<{len(frame)//2}h", frame)
                    rms = math.sqrt(sum(v * v for v in values) / len(values))
                    if rms >= script.get("vad_rms_threshold", 300):
                        if not speech_active:
                            speech_active = True
                            record("asr", operation="vad_start", turn=index + 1)
                            await ws.send_json(
                                {"type": "input_audio_buffer.speech_started"}
                            )
                        quiet_samples = 0
                    elif speech_active:
                        quiet_samples += len(values)
                        if quiet_samples >= round(
                            script.get("vad_silence_seconds", 0.6) * 16000
                        ):
                            record(
                                "asr",
                                operation="vad_endpoint",
                                turn=index + 1,
                                pcm_bytes=size,
                                quiet_samples=quiet_samples,
                            )
                            await ws.send_json(
                                {"type": "input_audio_buffer.speech_stopped"}
                            )
                            await finish_transcript()
            elif kind == "session.update":
                auto_vad = bool(body.get("session", {}).get("turn_detection"))
                await ws.send_json(
                    {"type": "session.updated", "session": body.get("session", {})}
                )
            elif kind == "input_audio_buffer.commit":
                record("asr", operation="commit", turn=index + 1, pcm_bytes=size)
                if not 32000 <= size <= 16000 * 2 * 120:
                    await ws.send_json(
                        {
                            "type": "error",
                            "error": {
                                "message": "require 1..120 seconds PCM16 at 16 kHz"
                            },
                        }
                    )
                    continue
                if not completed:
                    await finish_transcript()
        return ws

    async def chat(request):
        body = await request.json()
        messages = body.get("messages", [])
        last_user = next(
            (
                i
                for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") == "user"
            ),
            -1,
        )
        text = str(messages[last_user].get("content", "")) if last_user >= 0 else ""
        turn = next(
            (t for t in script["turns"] if t["text"] in text), script["turns"][0]
        )
        names = [t.get("function", {}).get("name") for t in body.get("tools", [])]
        results = [m for m in messages[last_user + 1 :] if m.get("role") == "tool"]
        record(
            "llm",
            model=body.get("model"),
            user=text,
            tool_results=len(results),
            available_tools=names,
        )
        speech = turn.get("reply", "好的，已完成本地测试。")
        tool_name, args = None, None
        if body.get("model") == "fake-intent":
            content = json.dumps(
                {"function_call": {"name": "continue_chat", "arguments": {}}}
            )
        else:
            steps = turn.get("tool_rounds", [])
            # Each scripted round is exactly one call; duplicate or missing names fail explicitly.
            step = steps[len(results)] if len(results) < len(steps) else None
            if step:
                tool_name, args = step["name"], dict(step.get("arguments", {}))
                if tool_name not in names:
                    return web.json_response(
                        {
                            "error": {
                                "message": f"Expected tool unavailable: {tool_name}"
                            }
                        },
                        status=422,
                    )
                args = {
                    "_aquamind_pre_speech": step.get("pre_speech", "我先查询一下。"),
                    **args,
                }
            elif "robot_output" in names:
                tool_name, args = "robot_output", {
                    "speech": speech,
                    "expression": "none",
                    "light": "none",
                    "product_refs": [],
                    "action": "none",
                }
            content = speech
        if not body.get("stream"):
            return web.json_response(
                {
                    "id": "fake-completion",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                }
            )
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)

        async def send(delta, finish=None):
            payload = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body.get("model", "fake-chat"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            await response.write(
                ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()
            )

        try:
            if turn.get("llm_metadata_first"):
                await send({"role": "assistant"})
            await asyncio.sleep(turn.get("llm_first_delay_ms", 120) / 1000)
            if tool_name:
                serialized = json.dumps(args, ensure_ascii=False)
                call_id = "call_" + uuid.uuid4().hex[:12]
                for offset in range(0, len(serialized), 8):
                    part = {
                        "index": 0,
                        "function": {"arguments": serialized[offset : offset + 8]},
                    }
                    if offset == 0:
                        part.update(id=call_id, type="function")
                        part["function"]["name"] = tool_name
                    await send({"tool_calls": [part]})
                    await asyncio.sleep(turn.get("llm_chunk_delay_ms", 15) / 1000)
            else:
                chunks = turn.get("llm_text_chunks") or [
                    content[offset : offset + 4] for offset in range(0, len(content), 4)
                ]
                for chunk in chunks:
                    await send({"content": chunk})
                    await asyncio.sleep(turn.get("llm_chunk_delay_ms", 15) / 1000)
            await send({}, "tool_calls" if tool_name else "stop")
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, RuntimeError):
            record("llm", operation="client_disconnected")
        return response

    async def tts(request):
        body = await request.json()
        text = body.get("text", "")
        if not text or body.get("audio_setting", {}).get("sample_rate", 24000) != 24000:
            return web.json_response(
                {"error": "expected text and 24 kHz PCM"}, status=400
            )
        record("tts", text=text, connection_id=hex(id(request.transport)))
        error = script.get("tts_error_status")
        if error:
            return web.json_response({"error": "injected"}, status=int(error))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        if script.get("tts_force_close"):
            response.force_close()
        await asyncio.sleep(script.get("tts_headers_delay_ms", 0) / 1000)
        await response.prepare(request)
        await asyncio.sleep(script.get("tts_first_delay_ms", 100) / 1000)
        duration = script.get("tts_duration_seconds", 3.6)
        data = (state.pcm * (int(duration * 48000) // len(state.pcm) + 1))[
            : int(duration * 48000)
        ]
        try:
            for _ in range(int(script.get("tts_empty_chunks", 0))):
                await response.write(b'data: {"data":{"status":1,"audio":""}}\n\n')
            for offset in range(0, len(data), 2880):
                payload = {
                    "data": {"status": 1, "audio": data[offset : offset + 2880].hex()},
                    "base_resp": {"status_code": 0},
                    "trace_id": "fake-tts",
                }
                await response.write(("data: " + json.dumps(payload) + "\n\n").encode())
                await asyncio.sleep(script.get("tts_chunk_delay_ms", 40) / 1000)
            await response.write(
                b'data: {"data":{"status":2},"base_resp":{"status_code":0}}\n\n'
            )
        except (ConnectionResetError, RuntimeError):
            record("tts", operation="client_disconnected")
        return response

    async def embeddings(request):
        body = await request.json()
        texts = body.get("input")
        texts = [texts] if isinstance(texts, str) else texts
        if (
            not isinstance(texts, list)
            or not texts
            or not all(isinstance(t, str) and t for t in texts)
        ):
            return web.json_response(
                {"error": "expected nonempty text input"}, status=400
            )
        dimensions = int(body.get("dimensions", 256))
        if not 2 <= dimensions <= 4096:
            return web.json_response({"error": "invalid dimensions"}, status=400)
        record("embedding", model=body.get("model"), batch_size=len(texts))
        await asyncio.sleep(script.get("embedding_delay_ms", 80) / 1000)
        if script.get("embedding_error_status"):
            return web.json_response(
                {"error": {"message": "injected embedding failure"}},
                status=int(script["embedding_error_status"]),
            )
        rows = []
        for index, text in enumerate(texts):
            blocked = "FAKE-BLOCKED" in text
            vector = [0.0, 1.0] if blocked else [1.0, 0.0]
            rows.append(
                {
                    "object": "embedding",
                    "index": index,
                    "embedding": vector + [0.0] * (dimensions - 2),
                }
            )
        return web.json_response(
            {
                "object": "list",
                "model": body.get("model"),
                "data": rows,
                "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
            }
        )

    app.router.add_put("/api/v1/api-key/by-key/configure", health)
    app.router.add_get("/health", health)
    app.router.add_get("/records", records)
    app.router.add_route("*", "/api/v1/openapi/device/{name}", ums)
    app.router.add_post("/v1/memory/{name}", memory)
    app.router.add_get("/asr", asr)
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_post("/v1/t2a_v2", tts)
    app.router.add_post("/v1/embeddings", embeddings)
    return app


def main():
    import argparse

    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19081)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--pcm-fixture", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    script = yaml.safe_load(args.script.read_text()) if args.script else None
    web.run_app(
        create_app(script, args.pcm_fixture),
        host="127.0.0.1",
        port=args.port,
        print=None,
    )


if __name__ == "__main__":
    main()
