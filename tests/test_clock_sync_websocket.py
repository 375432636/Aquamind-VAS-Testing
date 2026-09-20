import asyncio
import json
import time

from websockets.asyncio.server import serve

from voice_scenarios.clock_sync import ClockSyncSamples
from voice_scenarios.websocket import WebSocketTransport


def test_real_websocket_collects_both_groups_without_business_controls():
    async def exercise():
        controls, events = [], []

        async def peer(ws):
            async for raw in ws:
                message = json.loads(raw)
                controls.append(message["type"])
                if message["type"] == "hello":
                    await ws.send(
                        json.dumps(
                            dict(
                                type="hello",
                                session_id="s",
                                audio_params=dict(
                                    format="opus", channels=1, sample_rate=16000
                                ),
                            )
                        )
                    )
                elif message["type"] == "clock_sync":
                    stamp = str(time.time_ns() + 120_000_000)
                    await ws.send(
                        json.dumps(
                            dict(
                                type="clock_sync",
                                request_id=message["request_id"],
                                server_session_id="s",
                                server_received_ns=stamp,
                                server_sent_ns=stamp,
                            )
                        )
                    )

        async with serve(peer, "127.0.0.1", 0) as server:
            transport = WebSocketTransport(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", device_id="test"
            )
            await transport.connect(events.append)
            # Isolate calibration from the separately tested diagnostic handshake.
            transport.diagnostics = "stage"
            anchor = dict(monotonic_ns=time.monotonic_ns(), wall_time_ns=time.time_ns())
            transport.start_clock_sync(anchor)
            await transport.clock_task
            await transport.close()
        samples = [
            s
            for event in events
            if event.kind == "clock_sync_samples"
            for s in event.data["samples"]
        ]
        result = ClockSyncSamples(samples).summary()
        assert result["status"] == "calibrated"
        assert (
            abs(result["offset_ns"] - 120_000_000)
            < result["uncertainty_ns"] + 1_000_000
        )
        assert len(samples) == 10
        assert set(controls) == {"hello", "clock_sync"}

    asyncio.run(exercise())
