"""Local deterministic VAS peer for manual browser verification (no cloud traffic).

python -m tests.live_preview is not used; run PYTHONPATH=. python tests/live_preview.py
"""

import asyncio
import math
import struct
import time

import opuslib_next as opuslib
from aiohttp import web

from voice_scenarios.web_server import create_app


def app():
    application = create_app(
        "artifacts/live-preview", {"dev": "ws://127.0.0.1:19226/looomyn/v1/"}
    )

    async def peer(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        turn = 0
        task = None
        frames = 0

        async def say(text):
            await ws.send_json({"type": "stt", "text": text})
            await ws.send_json({"type": "tts", "state": "start"})
            await ws.send_json(
                {
                    "type": "tts",
                    "state": "sentence_start",
                    "text": "本地模拟回复：" + text,
                }
            )
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            for i in range(30):
                pcm = struct.pack(
                    "<960h",
                    *(
                        int(2500 * math.sin(2 * math.pi * 220 * (i * 960 + j) / 16000))
                        for j in range(960)
                    ),
                )
                await ws.send_bytes(encoder.encode(pcm, 960))
                await asyncio.sleep(0.025)
            await ws.send_json({"type": "tts", "state": "stop"})

        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                frames += 1
                if frames == 35 and task is None:
                    task = asyncio.create_task(say("模拟 VAD 识别结果"))
                continue
            if msg.type != web.WSMsgType.TEXT:
                continue
            m = msg.json()
            if m["type"] == "diagnostics":
                if m["state"] == "start":
                    await ws.send_json(
                        {
                            "type": "diagnostics",
                            "state": "started",
                            "level": "frame",
                            "schema_version": 1,
                            "server_session_id": "local-browser-peer",
                        }
                    )
                elif m["state"] == "finish":
                    await ws.send_json(
                        {
                            "type": "diagnostics",
                            "state": "events",
                            "schema_version": 1,
                            "server_session_id": "local-browser-peer",
                            "events": [],
                            "complete": True,
                            "finished": True,
                            "next_seq": 0,
                            "end_seq": 0,
                        }
                    )
                else:
                    await ws.send_json(
                        {
                            "type": "diagnostics",
                            "state": "error",
                            "error": "unsupported diagnostic control",
                        }
                    )
            elif m["type"] == "hello":
                await ws.send_json(
                    {
                        "type": "hello",
                        "session_id": "local-browser-peer",
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    }
                )
            elif m["type"] == "listen":
                if m["state"] == "start":
                    turn += 1
                    frames = 0
                    task = None
                elif m["state"] == "detect":
                    task = asyncio.create_task(say(m["text"]))
                elif m["state"] == "stop":
                    task = asyncio.create_task(say("模拟手动识别结果"))
            elif m["type"] == "abort":
                if task:
                    task.cancel()
                await ws.send_json({"type": "tts", "state": "stop"})
        if task:
            task.cancel()
        return ws

    application.router.add_get("/looomyn/v1/", peer)
    return application


if __name__ == "__main__":
    web.run_app(app(), host="127.0.0.1", port=19226)
