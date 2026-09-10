"""Start the actual VAS modules from an explicit safe local configuration."""

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import socket
import sys
from pathlib import Path


def loopback_only():
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def check(address):
        if isinstance(address, tuple):
            host = address[0]
            if host != "localhost":
                try:
                    allowed = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    allowed = False
                if not allowed:
                    raise PermissionError(
                        "Local fake stack blocked a non-loopback connection"
                    )

    def connect(self, address):
        check(address)
        return original(self, address)

    def connect_ex(self, address):
        check(address)
        return original_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vas-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    root = args.vas_root.resolve()
    config = json.loads(args.config.read_text())
    if config["server"]["ip"] != "127.0.0.1":
        raise ValueError("Development stack must bind loopback")
    fake_base = config["memory_api"]["base_url"].removesuffix("/v1/memory")
    os.environ.update(
        UMS_BASE_URL=fake_base,
        MEMORY_API_BASE_URL=fake_base + "/v1/memory",
        MEMORY_API_ENABLED="true",
        MEMORY_API_KEY="sk-fake-local-session",
        OPENAI_API_KEY="fake-local-key",
        LANGFUSE_TRACING_ENABLED="false",
        OTEL_SDK_DISABLED="true",
        NO_PROXY="*",
    )
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)
    loopback_only()
    sys.path = [
        p for p in sys.path if Path(p).resolve() != Path(__file__).parent.resolve()
    ]
    sys.path.insert(0, str(root))
    os.chdir(root)
    from core.utils.cache.manager import CacheType, cache_manager

    cache_manager.set(CacheType.CONFIG, "main_config", config)
    from config import settings

    settings.config_file_valid = True
    from core.websocket_server import WebSocketServer

    async def serve():
        server = WebSocketServer(config)
        original_handler = server._handle_connection
        admission = asyncio.Lock()

        async def single_script_session(ws):
            if admission.locked():
                await ws.close(
                    code=1013,
                    reason="Scripted local stack supports one connection; use another base-port for concurrency",
                )
                return
            async with admission:
                await original_handler(ws)

        server._handle_connection = single_script_session
        try:
            await server.start()
        finally:
            await server.close_active_connections()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
