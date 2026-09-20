import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from voice_scenarios.web_server import ENDPOINTS, create_app


def test_live_exposes_both_5090_network_paths(tmp_path):
    async def run():
        async with TestClient(TestServer(create_app(tmp_path))) as client:
            config = await (await client.get("/api/config")).json()
            assert config["endpoints"]["5090-tailscale"] == (
                "wss://100.114.113.70:18443/looomyn/v1/"
            )
            assert config["endpoints"]["5090-lan"] == (
                "wss://10.10.95.179:18443/looomyn/v1/"
            )
            page = await (await client.get("/live/")).text()
            assert 'id="5090-address"' in page
            assert "100.114.113.70" in page
            assert "10.10.95.179" in page
            for environment in ("5090-tailscale", "5090-lan"):
                response = await client.post(
                    "/api/sessions",
                    json={
                        "environment": environment,
                        "device_id": "00:00:00:00:00:21",
                        "recording": False,
                    },
                )
                assert response.status == 200
                assert (await response.json())["ws_url"] == ENDPOINTS[environment]

    asyncio.run(run())


def test_live_chat_only_does_not_create_recording_or_report(tmp_path):
    async def run():
        async with TestClient(TestServer(create_app(tmp_path))) as client:
            response = await client.post(
                "/api/sessions",
                json={
                    "environment": "dev",
                    "device_id": "00:00:00:00:00:21",
                    "recording": False,
                },
            )
            assert response.status == 200
            session = await response.json()
            assert session == {
                "ws_url": "wss://lumin-vas-aquamind-dev.deep-edge.cn/looomyn/v1/"
            }
            assert not list(tmp_path.iterdir())
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


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
                        event(
                            "turn_started",
                            2000000000,
                            1,
                            mode="text",
                            text="你好",
                            server_listen_turn_id=7,
                        ),
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
            assert saved["turns"][0]["server_listen_turn_id"] == 7
            assert saved["diagnostics"]["complete"] is False

    asyncio.run(run())
