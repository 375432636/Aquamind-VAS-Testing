"""Authenticated Docker entry point; also serves reports before live UI ships."""

import base64
import binascii
import hmac
import html
import importlib.util
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from aiohttp import web


def create_app(output, username, password, revision, *, live=None):
    if not username or not password:
        raise ValueError("Deployment requires credentials")
    expected = f"{username}:{password}".encode()
    recording_key = secrets.token_bytes(32)
    recording_ttl = 8 * 3600

    def signed_recording_cookie(expiry):
        signature = hmac.digest(recording_key, expiry.encode(), "sha256").hex()
        return f"{expiry}.{signature}"

    def recording_authenticated(request):
        # Some tunnels omit Authorization on upgrades. A short-lived cookie
        # issued after Basic login authenticates only same-origin recording.
        if (
            request.headers.get("Upgrade", "").lower() != "websocket"
            or not request.path.startswith("/api/sessions/")
            or not request.path.endswith("/record")
            or urlsplit(request.headers.get("Origin", "")).netloc != request.host
        ):
            return False
        value = request.cookies.get("voice_lab_recording", "")
        expiry = value.partition(".")[0]
        return (
            value.isascii()
            and expiry.isdigit()
            and len(expiry) <= 12
            and time.time() < int(expiry) <= time.time() + recording_ttl
            and secrets.compare_digest(value, signed_recording_cookie(expiry))
        )

    @web.middleware
    async def authenticate(request, handler):
        basic_authenticated = False
        if request.path != "/healthz":
            header = request.headers.get("Authorization", "")
            try:
                scheme, value = header.split(" ", 1)
                supplied = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                scheme, supplied = "", b""
            basic_authenticated = scheme.lower() == "basic" and secrets.compare_digest(
                supplied, expected
            )
            if not basic_authenticated and not recording_authenticated(request):
                raise web.HTTPUnauthorized(
                    headers={"WWW-Authenticate": 'Basic realm="Aquamind Voice Lab"'}
                )
        response = await handler(request)
        if (
            basic_authenticated
            and request.path == "/api/sessions"
            and response.status == 200
        ):
            response.set_cookie(
                "voice_lab_recording",
                signed_recording_cookie(str(int(time.time()) + recording_ttl)),
                max_age=recording_ttl,
                path="/api/sessions/",
                secure=True,
                httponly=True,
                samesite="Strict",
            )
        return response

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if live is None:
        live = importlib.util.find_spec("voice_scenarios.web_server") is not None
    if live:
        from voice_scenarios.web_server import create_app as live_app

        app = live_app(output)
        app.middlewares.insert(0, authenticate)
    else:
        app = web.Application(middlewares=[authenticate])

        async def index(request):
            reports = sorted(
                output.glob("**/report.html"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            links = "".join(
                f'<li><a href="/reports/{quote(str(p.relative_to(output)))}">'
                f"{html.escape(str(p.parent.relative_to(output)))}</a></li>"
                for p in reports
                if not p.is_symlink()
            )
            page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            page += "<title>Aquamind VAS Testing</title><style>body{max-width:900px;margin:60px auto;padding:24px;font:16px/1.8 system-ui;color:#24334b}a{color:#305bcc}small{color:#64748b}</style>"
            page += "<h1>Aquamind VAS Testing</h1><p>测试工具已部署。当前 main 提供命令行测试与静态报告；网页版合入 main 后会自动启用。</p>"
            page += "<h2>会话报告</h2><ul>" + (links or "<li>暂无报告</li>") + "</ul>"
            page += f"<small>版本：{html.escape(revision)}</small></html>"
            return web.Response(text=page, content_type="text/html")

        app.router.add_get("/", index)
        app.router.add_static("/reports/", output, show_index=False)

    async def health(request):
        return web.json_response(
            {"status": "ok", "revision": revision, "live": bool(live)}
        )

    app.router.add_get("/healthz", health)
    return app


if __name__ == "__main__":
    web.run_app(
        create_app(
            os.environ.get("REPORT_DIR", "/data/reports"),
            os.environ.get("WEB_USERNAME", ""),
            os.environ.get("WEB_PASSWORD", ""),
            os.environ.get("APP_REVISION", "unknown"),
        ),
        host="0.0.0.0",
        port=8080,
    )
