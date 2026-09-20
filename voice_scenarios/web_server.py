"""Local browser UI and recording side channel, never an audio relay to VAS."""

import asyncio
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import ClientSession, WSMsgType, web

from .interactive_session import InteractiveSession

ENDPOINTS = {
    "dev": "wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/",
    "main": "wss://lumin-vas-aquamind.deep-edge.cn/looomyn/v1/",
    "5090-tailscale": "wss://100.114.113.70:18443/looomyn/v1/",
    "5090-lan": "wss://10.10.95.179:18443/looomyn/v1/",
}


@web.middleware
async def local_origin(request, handler):
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        raise web.HTTPForbidden(text="Use the local live page")
    response = await handler(request)
    if request.path == "/" or request.path.startswith("/live/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def create_app(output, endpoints=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    assets = Path(__file__).parent / "live"
    endpoints = endpoints or ENDPOINTS
    sessions = {}
    app = web.Application(middlewares=[local_origin], client_max_size=4 * 1024**2)

    async def config(request):
        return web.json_response({"endpoints": endpoints})

    async def start(request):
        import re

        config = await request.json()
        if config.get("environment") not in endpoints or not re.fullmatch(
            r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", config.get("device_id", "")
        ):
            raise web.HTTPBadRequest(text="请选择环境并填写有效 MAC")
        if config.get("recording", True) is False:
            return web.json_response({"ws_url": endpoints[config["environment"]]})
        sid = uuid.uuid4().hex
        sessions[sid] = InteractiveSession(output / sid, config)
        return web.json_response(
            {
                "id": sid,
                "record_url": f"/api/sessions/{sid}/record",
                "ws_url": endpoints[config["environment"]],
            }
        )

    async def record(request):
        sid = request.match_info["sid"]
        if sid not in sessions or sessions[sid].closed:
            raise web.HTTPNotFound()
        session = sessions[sid]
        if getattr(session, "connected", False):
            raise web.HTTPConflict(text="Recorder already attached")
        session.connected = True
        ws = web.WebSocketResponse(max_msg_size=4 * 1024**2, heartbeat=30)
        await ws.prepare(request)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                value = json.loads(msg.data)
                if value.get("action") == "finish":
                    await session.finish(value.get("complete", True))
                    await ws.send_json(
                        {
                            "state": "finished",
                            "report": f"/reports/{sid}/report.html",
                            "excel": f"/reports/{sid}/evaluation.xlsx",
                        }
                    )
                    break
                for event in value.get("events", []):
                    session.accept(event)
                await ws.send_json(
                    {"state": "saved", "sequence": value.get("sequence")}
                )
        except (ValueError, KeyError, TypeError) as exc:
            await ws.send_json({"state": "error", "message": str(exc)})
        finally:
            try:
                await session.finish(False)
            finally:
                await ws.close()
        return ws

    async def ota(request):
        data = await request.json()
        env = data.get("environment")
        if env not in endpoints:
            raise web.HTTPBadRequest()
        uri = urlsplit(endpoints[env])
        scheme = "https" if uri.scheme == "wss" else "http"
        headers = {
            "Device-Id": data.get("device_id", ""),
            "Client-Id": "aquamind-console",
            "Content-Type": "application/json",
        }
        if data.get("token"):
            headers["Authorization"] = "Bearer " + data["token"]
        async with ClientSession() as client:
            async with client.post(
                f"{scheme}://{uri.netloc}/looomyn/ota/",
                json=data.get("body", {}),
                headers=headers,
                timeout=15,
            ) as response:
                return web.Response(
                    status=response.status,
                    body=await response.read(),
                    content_type="application/json",
                )

    async def index(request):
        return web.FileResponse(assets / "index.html")

    async def cleanup(app):
        for session in sessions.values():
            await session.finish(False)

    app.on_cleanup.append(cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/live/", index)
    app.router.add_get("/api/config", config)
    app.router.add_post("/api/sessions", start)
    app.router.add_get("/api/sessions/{sid}/record", record)
    app.router.add_post("/api/ota", ota)
    app.router.add_static("/live/assets/", assets)
    app.router.add_static("/reports/", output, show_index=False)
    return app


def serve(output, host="127.0.0.1", port=19225):
    web.run_app(create_app(output), host=host, port=port)
