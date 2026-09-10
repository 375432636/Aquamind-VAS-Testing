import asyncio
import base64
import json

import aiohttp

from voice_scenarios.fake_services import create_app


def test_fake_asr_vad_requires_real_audio_then_continuous_quiet_not_commit():
    async def exercise():
        runner = aiohttp.web.AppRunner(
            create_app(
                {
                    "vad_silence_seconds": 0.18,
                    "turns": [{"text": "自动结束", "asr_final_delay_ms": 0}],
                }
            )
        )
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            async with (
                aiohttp.ClientSession() as client,
                client.ws_connect(url + "/asr") as ws,
            ):
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "session.update",
                        "session": {"turn_detection": {"type": "server_vad"}},
                    }
                )
                await ws.receive_json()

                async def append(pcm):
                    await ws.send_json(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm).decode(),
                        }
                    )

                await append(b"\x05\x00" * 4800)  # quiet pre-roll must not finish
                await append(b"\x00\x10" * 19200)
                assert (await asyncio.wait_for(ws.receive_json(), 1))[
                    "type"
                ] == "input_audio_buffer.speech_started"
                pending = asyncio.create_task(ws.receive_json())
                await asyncio.sleep(
                    0.2
                )  # network silence must NOT substitute audio silence
                assert not pending.done()
                await append(b"\x05\x00" * 960)
                await append(b"\x05\x00" * 960)
                await append(b"\x05\x00" * 960)
                assert (await asyncio.wait_for(pending, 1))[
                    "type"
                ] == "input_audio_buffer.speech_stopped"
                assert (await ws.receive_json())["transcript"] == "自动结束"
                async with client.get(url + "/records") as response:
                    records = await response.json()
                assert not any(r.get("operation") == "commit" for r in records)
                assert any(r.get("operation") == "vad_endpoint" for r in records)
        finally:
            await runner.cleanup()

    asyncio.run(exercise())


def test_fake_asr_requires_audio_commit_and_openai_fragments_tool_arguments():
    async def exercise():
        app = create_app()
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
        try:
            async with aiohttp.ClientSession() as client:
                async with client.ws_connect(url + "/asr") as ws:
                    assert (await ws.receive_json())["type"] == "session.created"
                    await ws.send_json(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(b"\0\1" * 19200).decode(),
                        }
                    )
                    await ws.send_json({"type": "input_audio_buffer.commit"})
                    final = await ws.receive_json()
                    assert (
                        final["type"]
                        == "conversation.item.input_audio_transcription.completed"
                    )
                    assert final["transcript"]
                async with client.post(
                    url + "/v1/chat/completions",
                    json={
                        "model": "fake-chat",
                        "stream": True,
                        "messages": [{"role": "user", "content": "你好"}],
                        "tools": [
                            {"type": "function", "function": {"name": "robot_output"}}
                        ],
                    },
                ) as response:
                    chunks = [
                        json.loads(line[6:])
                        for line in (await response.text()).splitlines()
                        if line.startswith("data: {")
                    ]
                    calls = [c["choices"][0]["delta"].get("tool_calls") for c in chunks]
                    assert sum(bool(c) for c in calls) > 1
                    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
        finally:
            await runner.cleanup()

    asyncio.run(exercise())


def test_fake_ums_memory_tts_and_invalid_requests(tmp_path):
    import wave

    fixture = tmp_path / "fixture.wav"
    with wave.open(str(fixture), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\1" * 2400)

    async def exercise():
        runner = aiohttp.web.AppRunner(
            create_app(
                {
                    "turns": [
                        {
                            "text": "工具",
                            "reply": "完成",
                            "tool_rounds": [
                                {
                                    "name": "self_test_weather",
                                    "arguments": {"city": "上海"},
                                }
                            ],
                        }
                    ],
                    "tts_duration_seconds": 0.06,
                    "tts_first_delay_ms": 0,
                },
                fixture,
            )
        )
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
        try:
            async with aiohttp.ClientSession() as client:
                async with client.post(
                    url + "/api/v1/openapi/device/token",
                    json={"device_fingerprint": "device"},
                ) as r:
                    assert (await r.json())["code"] == 200
                for path in ("apikey", "prompt"):
                    async with client.get(url + "/api/v1/openapi/device/" + path) as r:
                        assert (await r.json())["code"] == 200
                async with client.get(url + "/api/v1/openapi/device/unknown") as r:
                    assert r.status == 404
                async with client.post(
                    url + "/v1/memory/get_memory", json={"query": "test"}
                ) as r:
                    assert (await r.json())["status"] == "success"
                async with client.post(url + "/v1/t2a_v2", json={"text": "hello"}) as r:
                    data = await r.text()
                    assert '"status": 1' in data and '"status":2' in data
                async with client.post(url + "/v1/t2a_v2", json={"text": ""}) as r:
                    assert r.status == 400
                async with client.post(
                    url + "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "工具"}],
                        "tools": [],
                        "stream": True,
                    },
                ) as r:
                    assert r.status == 422
                async with client.post(
                    url + "/v1/chat/completions", json={"model": "fake-intent"}
                ) as r:
                    assert (
                        "continue_chat"
                        in (await r.json())["choices"][0]["message"]["content"]
                    )
                async with client.get(url + "/health") as r:
                    assert (await r.json())["fake"]
                async with client.get(url + "/records") as r:
                    assert len(await r.json()) >= 7
        finally:
            await runner.cleanup()

    asyncio.run(exercise())
