import asyncio
import base64
import importlib.util
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer


def load_server():
    spec = importlib.util.spec_from_file_location(
        "deployment_server", Path(__file__).parents[1] / "scripts/serve_deployment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_deployment_requires_login_and_serves_saved_reports(tmp_path):
    async def check():
        server = load_server()
        (tmp_path / "session-1").mkdir()
        (tmp_path / "session-1" / "report.html").write_text("test report")
        app = server.create_app(
            tmp_path, "tester", "a-test-password", "abc123", live=False
        )
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/healthz")
            assert response.status == 200
            assert (await response.json())["revision"] == "abc123"
            for path in ("/", "/reports/session-1/report.html"):
                assert (await client.get(path)).status == 401
            headers = {
                "Authorization": "Basic "
                + base64.b64encode(b"tester:a-test-password").decode()
            }
            response = await client.get("/", headers=headers)
            assert response.status == 200
            assert "session-1/report.html" in await response.text()
            response = await client.get(
                "/reports/session-1/report.html", headers=headers
            )
            assert await response.text() == "test report"
            assert (
                await client.get("/reports/../serve_deployment.py", headers=headers)
            ).status == 404

    asyncio.run(check())


def test_deployment_refuses_empty_password(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="credentials"):
        load_server().create_app(tmp_path, "tester", "", "abc123", live=False)


def test_deployment_auto_enables_live_ui_and_keeps_recording_authenticated(tmp_path):
    async def check():
        app = load_server().create_app(tmp_path, "tester", "test-only", "live123")
        headers = {
            "Authorization": "Basic " + base64.b64encode(b"tester:test-only").decode()
        }
        async with TestClient(TestServer(app)) as client:
            health = await (await client.get("/healthz")).json()
            assert health == {"status": "ok", "revision": "live123", "live": True}
            assert (await client.get("/live/")).status == 401
            assert (await client.post("/api/sessions", json={})).status == 401
            response = await client.get("/live/", headers=headers)
            assert response.status == 200
            assert "app.mjs" in await response.text()
            response = await client.post(
                "/api/sessions",
                json={"environment": "dev", "device_id": "00:00:00:00:00:21"},
                headers=headers,
            )
            assert response.status == 200
            session = await response.json()
            assert (await client.get(session["record_url"])).status == 401
            ws = await client.ws_connect(session["record_url"], headers=headers)
            await ws.send_json({"action": "finish"})
            result = await ws.receive_json()
            assert result["state"] == "finished"
            for key in ("report", "excel"):
                assert (await client.get(result[key])).status == 401
                assert (await client.get(result[key], headers=headers)).status == 200
            await ws.close()

    asyncio.run(check())


def test_recording_uses_signed_cookie_when_tunnel_strips_basic_auth(
    tmp_path, monkeypatch
):
    async def check():
        server = load_server()
        app = server.create_app(tmp_path, "tester", "test-only", "live123")
        auth = {
            "Authorization": "Basic " + base64.b64encode(b"tester:test-only").decode()
        }
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/sessions",
                json={"environment": "dev", "device_id": "00:00:00:00:00:21"},
                headers=auth,
            )
            session = await response.json()
            cookie = response.cookies["voice_lab_recording"]
            assert cookie["secure"] and cookie["httponly"]
            assert cookie["samesite"] == "Strict"
            headers = {
                "Cookie": f"voice_lab_recording={cookie.value}",
                "Origin": str(client.make_url("/")).rstrip("/"),
            }
            # This credential is limited to same-origin WebSocket recording.
            assert (await client.get("/api/config", headers=headers)).status == 401
            for bad in [
                {**headers, "Origin": "https://other.example"},
                {**headers, "Cookie": "voice_lab_recording=invalid"},
            ]:
                response = await client.get(
                    session["record_url"], headers={**bad, "Upgrade": "websocket"}
                )
                assert response.status in (401, 403)
            now = server.time.time()
            monkeypatch.setattr(server.time, "time", lambda: now + 9 * 3600)
            assert (
                await client.get(
                    session["record_url"], headers={**headers, "Upgrade": "websocket"}
                )
            ).status == 401
            monkeypatch.setattr(server.time, "time", lambda: now)
            ws = await client.ws_connect(session["record_url"], headers=headers)
            await ws.send_json({"action": "finish"})
            result = await ws.receive_json()
            assert result["state"] == "finished"
            assert (await client.get(result["report"], headers=auth)).status == 200
            await ws.close()

    asyncio.run(check())
