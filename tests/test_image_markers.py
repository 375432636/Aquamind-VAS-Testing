import copy
import json
import re

from voice_scenarios.report import build_report


def payload(path):
    return json.loads(
        re.search(
            r'<script id="data" type="application/json">(.*?)</script>',
            path.read_text(),
            re.S,
        )[1]
    )


def test_old_image_messages_get_client_markers_without_changing_server_clock(tmp_path):
    report = {
        "name": "图片时序",
        "status": "passed",
        "turns": [
            {
                "id": "one",
                "status": "completed",
                "events": [
                    {
                        "event": "image",
                        "at_ns": 12_000_000_000,
                        "data": {
                            "type": "image",
                            "url": "https://example.com/a.png",
                            "session_id": "s",
                        },
                    },
                    {
                        "event": "image",
                        "at_ns": 12_001_000_000,
                        "data": {"type": "image", "url": "https://example.com/a.png"},
                    },
                ],
            },
            {
                "id": "two",
                "status": "completed",
                "events": [
                    {
                        "event": "image",
                        "at_ns": 17_000_000_000,
                        "data": {"type": "image", "url": "javascript:alert(1)"},
                    },
                ],
            },
        ],
        "session_playback": {
            "zero_at_ns": 10_000_000_000,
            "duration_seconds": 10,
            "turns": [],
            "segments": [],
            "markers": [],
        },
    }
    original = copy.deepcopy(report)
    build_report(report, tmp_path / "report.html")
    overview = payload(tmp_path / "report.html")
    first = payload(tmp_path / "turn-001.html")["turn"]
    second = payload(tmp_path / "turn-002.html")["turn"]
    assert [m["at_seconds"] for m in overview["session_playback"]["image_markers"]] == [
        2,
        2.001,
        7,
    ]
    assert [m["turn_index"] for m in overview["session_playback"]["image_markers"]] == [
        1,
        1,
        2,
    ]
    assert len(first["image_markers"]) == 2
    assert len(second["image_markers"]) == 1
    assert first["image_markers"][0]["start_ns"] == 12_000_000_000
    assert first["timeline_lanes"] == []
    assert report == original


def test_websocket_keeps_actual_image_messages_with_client_receive_times():
    import asyncio

    from websockets.asyncio.server import serve

    from voice_scenarios.websocket import WebSocketTransport

    async def exercise():
        rows = []
        ready = asyncio.Event()

        def emit(event):
            rows.append(event)
            if sum(row.kind == "image" for row in rows) == 2:
                ready.set()

        async def peer(ws):
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "session_id": "image-session",
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    }
                )
            )
            for url in ("https://example.com/one.png", "https://example.com/two.png"):
                await ws.send(
                    json.dumps(
                        {"type": "image", "url": url, "session_id": "image-session"}
                    )
                )
            await ws.wait_closed()

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                device_id="image-test",
            )
            try:
                await transport.connect(emit)
                await asyncio.wait_for(ready.wait(), 2)
            finally:
                await transport.close()
        messages = [row for row in rows if row.kind == "image"]
        assert [row.data["url"] for row in messages] == [
            "https://example.com/one.png",
            "https://example.com/two.png",
        ]
        assert all(row.at_ns > 0 for row in messages)
        assert messages[1].at_ns >= messages[0].at_ns

    asyncio.run(exercise())
