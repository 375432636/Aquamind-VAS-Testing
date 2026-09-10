"""Explicit device MCP fixtures; music states describe a simulated player."""

import asyncio
import json

from .protocol import Event


def tool(name, description, properties, required):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


TOOLS = [
    tool("self_test_weather", "测试天气查询", {"city": {"type": "string"}}, ["city"]),
    tool(
        "self_test_rag_query",
        "测试知识库检索",
        {"user_query": {"type": "string"}},
        ["user_query"],
    ),
    tool(
        "self_music_play",
        "播放测试音乐 fixture",
        {"track": {"type": "string", "enum": ["fixture"]}},
        ["track"],
    ),
    tool("self_music_stop", "停止测试音乐", {}, []),
]


class FakeDevice:
    def __init__(self, emit):
        self.emit = emit
        self.music = None

    async def call(self, name, arguments, request_id):
        definition = next((t for t in TOOLS if t["name"] == name), None)
        if not definition or not isinstance(arguments, dict):
            raise ValueError("unknown tool or invalid arguments")
        schema = definition["inputSchema"]
        if set(arguments) - set(schema["properties"]) or any(
            not isinstance(arguments.get(key), str) or not arguments[key]
            for key in schema["required"]
        ):
            raise ValueError("arguments do not match fixture schema")
        metadata = {
            "name": name,
            "request_id": request_id,
            "source": "simulated_device_mcp",
        }
        self.emit(Event("mcp_call_received", metadata))
        await asyncio.sleep(0.08)
        if name == "self_music_play":
            if arguments["track"] != "fixture":
                raise ValueError("unknown fixture track")
            await self.stop("replaced")
            self.emit(Event("music_call_accepted", metadata))
            self.music = asyncio.create_task(self._music(metadata))
            result = {
                "status": "accepted",
                "track": "fixture",
                "playback_source": "simulated_player",
            }
        elif name == "self_music_stop":
            await self.stop("tool")
            result = {"status": "stopped", "playback_source": "simulated_player"}
        elif name == "self_test_weather":
            result = {
                "city": arguments["city"],
                "temperature_c": 26,
                "condition": "晴",
                "fixture": True,
            }
        else:
            result = {
                "matches": [
                    {"title": "测试知识库", "content": "开放时间为每天九点至十七点。"}
                ],
                "fixture": True,
            }
        self.emit(Event("mcp_result_sent", metadata))
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
            ],
            "isError": False,
        }

    async def _music(self, metadata):
        try:
            await asyncio.sleep(0.12)
            self.emit(Event("music_playback_started", metadata))
            await asyncio.sleep(60)
            self.emit(Event("music_playback_stopped", {**metadata, "reason": "ended"}))
        except asyncio.CancelledError:
            self.emit(
                Event("music_playback_stopped", {**metadata, "reason": "cancelled"})
            )
            raise

    async def stop(self, reason):
        if self.music:
            self.emit(
                Event(
                    "music_stop_requested",
                    {"reason": reason, "source": "simulated_player"},
                )
            )
            self.music.cancel()
            await asyncio.gather(self.music, return_exceptions=True)
            self.music = None
