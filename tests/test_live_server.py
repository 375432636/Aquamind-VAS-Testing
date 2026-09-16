import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from voice_scenarios.web_server import create_app


def test_live_records_and_exports_without_vas(tmp_path):
    async def run():
        async with TestClient(TestServer(create_app(tmp_path))) as client:
            response = await client.get("/live/")
            assert response.status == 200
            assert "app.mjs" in await response.text()
            assert (await client.get("/live/assets/app.mjs")).status == 200
            assert (
                await client.post(
                    "/api/sessions", json={"environment": "wrong", "device_id": "bad"}
                )
            ).status == 400
            assert (
                await client.post(
                    "/api/sessions",
                    headers={"Origin": "https://other.example"},
                    json={},
                )
            ).status == 403
            response = await client.post(
                "/api/sessions",
                json={"environment": "dev", "device_id": "00:00:00:00:00:21"},
            )
            session = await response.json()
            ws = await client.ws_connect(session["record_url"])

            def event(name, at, turn=0, **data):
                return dict(
                    event=name,
                    at_ns=at,
                    wall_time_ns=1700000000000000000 + at,
                    turn=turn,
                    data=data,
                )

            await ws.send_json(
                {
                    "sequence": 1,
                    "events": [
                        event("connection_started", 1000000000),
                        event("turn_started", 2000000000, 1, mode="text", text="你好"),
                        event("text_sent", 2000000000, 1),
                        event("turn_finished", 3000000000, 1),
                    ],
                }
            )
            assert (await ws.receive_json())["state"] == "saved"
            await ws.send_json({"action": "finish"})
            result = await ws.receive_json()
            assert result["state"] == "finished"
            for name in ("report", "excel"):
                assert (await client.get(result[name])).status == 200
            saved = json.loads((tmp_path / session["id"] / "result.json").read_text())
            assert saved["turns"][0]["input_text"] == "你好"
            assert saved["diagnostics"]["complete"] is False

    asyncio.run(run())
