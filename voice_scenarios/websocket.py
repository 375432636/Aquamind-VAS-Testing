"""The device WebSocket boundary; no VAS process is started by this client."""

import asyncio
import json
import time
import wave
from pathlib import Path

import opuslib_next as opuslib
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .fake_device import TOOLS, FakeDevice
from .protocol import Event


class WebSocketTransport:
    def __init__(
        self,
        url,
        *,
        device_id,
        token=None,
        diagnostics="off",
        connect_timeout=15,
        fake_device_tools=False,
    ):
        if diagnostics not in {"off", "stage", "frame"}:
            raise ValueError("diagnostics must be off, stage or frame")
        self.url = url
        self.device_id = device_id
        self.token = token
        self.diagnostics = diagnostics
        self.connect_timeout = connect_timeout
        self.fake_device_tools = fake_device_tools
        self.mcp_tasks = set()
        self.ws = None
        self.reader = None
        self.closing = False
        self.diagnostics_active = False
        self.diagnostics_ended = asyncio.Event()
        self.turn_sequence = 0
        self.audio_sequence = 0
        self.microphone_task = None
        self.pending_response_turn = None
        self.output_response_turn = None
        self.output_active = False

    async def connect(self, emit):
        self.emit = emit
        self.device = FakeDevice(emit)
        self.hello = asyncio.get_running_loop().create_future()
        self.diagnostics_ready = (
            asyncio.get_running_loop().create_future()
            if self.diagnostics != "off"
            else None
        )
        headers = {"Device-Id": self.device_id, "Client-Id": "voice-scenarios"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        self.emit(Event("connection_started"))
        self.ws = await connect(
            self.url,
            additional_headers=headers,
            open_timeout=self.connect_timeout,
            max_size=4 * 1024 * 1024,
        )
        self.emit(Event("connection_opened"))
        self.reader = asyncio.create_task(self._receive())
        if self.diagnostics != "off":
            await self._send_json(
                {"type": "diagnostics", "state": "start", "level": self.diagnostics}
            )
            await asyncio.wait_for(self.diagnostics_ready, self.connect_timeout)
        await self._send_json(
            {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "features": {"mcp": True},
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            }
        )
        return await asyncio.wait_for(self.hello, self.connect_timeout)

    async def _send_json(self, message):
        await self.ws.send(json.dumps(message, ensure_ascii=False))

    async def _receive(self):
        decoder = None
        try:
            async for raw in self.ws:
                at_ns = time.monotonic_ns()
                if isinstance(raw, bytes):
                    if decoder is None:
                        raise ValueError("binary_audio_before_hello")
                    self.audio_sequence += 1
                    ownership = {
                        "response_listen_turn_id": self.output_response_turn,
                        "is_session_output": self.output_response_turn is None,
                    }
                    self.emit(
                        Event(
                            "audio_packet_received",
                            {
                                "audio_seq": self.audio_sequence,
                                "bytes": len(raw),
                                **ownership,
                            },
                            at_ns,
                        )
                    )
                    pcm = decoder.decode(raw, self.output_rate * 120 // 1000)
                    self.emit(
                        Event(
                            "pcm",
                            {
                                "pcm": pcm,
                                "sample_rate": self.output_rate,
                                "audio_seq": self.audio_sequence,
                                "received_at_ns": at_ns,
                                **ownership,
                            },
                        )
                    )
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "diagnostics":
                    state = message.get("state")
                    if state == "started":
                        if (
                            message.get("schema_version") != 1
                            or message.get("level") != self.diagnostics
                        ):
                            raise ValueError("unsupported_diagnostic_negotiation")
                        self.diagnostics_active = True
                        self.emit(Event("diagnostics_started", message, at_ns))
                        if (
                            self.diagnostics_ready is not None
                            and not self.diagnostics_ready.done()
                        ):
                            self.diagnostics_ready.set_result(message)
                    elif state == "events":
                        self.emit(Event("diagnostics", message, at_ns))
                        if message.get("finished") and message.get(
                            "next_seq"
                        ) == message.get("end_seq"):
                            self.diagnostics_ended.set()
                    elif state == "error":
                        raise ValueError(
                            "diagnostics_rejected: " + str(message.get("error"))
                        )
                    else:
                        raise ValueError("unknown_diagnostic_message")
                elif kind == "hello":
                    params = message.get("audio_params", {})
                    self.output_rate = params.get("sample_rate", 16000)
                    if (
                        params.get("format") != "opus"
                        or params.get("channels", 1) != 1
                        or self.output_rate not in {8000, 12000, 16000, 24000, 48000}
                    ):
                        raise ValueError("unsupported_output_audio_format")
                    decoder = opuslib.Decoder(self.output_rate, 1)
                    session = {
                        "session_id": message.get("session_id"),
                        "audio_params": params,
                        "transport": "websocket",
                        "diagnostics": self.diagnostics,
                    }
                    if not self.hello.done():
                        self.hello.set_result(session)
                    self.emit(Event("hello_received", session, at_ns))
                elif kind == "tts":
                    state = message.get("state")
                    if state == "start" and not self.output_active:
                        # VAS sends STT before a question's reply. Hello's greeting
                        # has no STT. Latch the stream until stop: STT and duplicate
                        # starts can arrive while the greeting is still playing.
                        self.output_response_turn = self.pending_response_turn
                        self.output_active = True
                    self.emit(
                        Event(
                            "tts_" + str(state),
                            {
                                **message,
                                "response_listen_turn_id": self.output_response_turn,
                                "is_session_output": self.output_response_turn is None,
                            },
                            at_ns,
                        )
                    )
                    if state == "stop":
                        self.output_active = False
                elif kind == "stt":
                    self.pending_response_turn = self.turn_sequence
                    self.emit(Event("stt", message, at_ns))
                elif kind == "mcp":
                    await self._mcp(message)
                else:
                    self.emit(Event(str(kind or "control"), message, at_ns))
        except Exception as exc:
            if not self.closing:
                self.emit(Event("error", {"message": f"websocket_receive: {exc}"}))
                if not self.hello.done():
                    self.hello.set_exception(exc)
                if (
                    self.diagnostics_ready is not None
                    and not self.diagnostics_ready.done()
                ):
                    self.diagnostics_ready.set_exception(exc)
        finally:
            self.diagnostics_ended.set()
            if self.diagnostics_ready is not None and not self.diagnostics_ready.done():
                self.diagnostics_ready.set_exception(
                    ConnectionError("diagnostic_connection_closed")
                )
            if not self.closing:
                self.emit(Event("disconnected"))

    async def _mcp(self, message):
        payload = message.get("payload", {})
        request_id = payload.get("id")
        if request_id is None:
            return
        response = {"jsonrpc": "2.0", "id": request_id}
        method = payload.get("method")
        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "voice-scenarios", "version": "0.1.0"},
            }
        elif method == "tools/list":
            response["result"] = {"tools": TOOLS if self.fake_device_tools else []}
        elif method == "tools/call" and self.fake_device_tools:

            async def call_tool():
                try:
                    params = payload.get("params", {})
                    response["result"] = await self.device.call(
                        params.get("name"), params.get("arguments", {}), request_id
                    )
                except ValueError as exc:
                    response["result"] = {
                        "isError": True,
                        "content": [{"type": "text", "text": str(exc)}],
                    }
                await self._send_json({"type": "mcp", "payload": response})

            task = asyncio.create_task(call_tool())
            self.mcp_tasks.add(task)
            task.add_done_callback(self.mcp_tasks.discard)
            return
        else:
            response["error"] = {
                "code": -32601,
                "message": "Unsupported test MCP method",
            }
        await self._send_json({"type": "mcp", "payload": response})

    async def send_audio(self, path: Path, *, input_stream=None, uplink_path=None):
        if input_stream is not None and input_stream.mode == "vad":
            return await self._send_vad_audio(path, input_stream, uplink_path)
        with wave.open(str(path), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 16000
                or audio.getcomptype() != "NONE"
                or not audio.getnframes()
            ):
                raise ValueError("input must be nonempty mono PCM16 WAV at 16000 Hz")
            samples = audio.getnframes()
            encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
            self.turn_sequence += 1
            await self._send_json(
                {"type": "listen", "state": "start", "mode": "manual"}
            )
            self.emit(
                Event("listen_start_sent", {"listen_turn_id": self.turn_sequence})
            )
            started = time.monotonic()
            count = 0
            while pcm := audio.readframes(960):
                await asyncio.sleep(max(0, started + count * 0.06 - time.monotonic()))
                packet = encoder.encode(pcm.ljust(1920, b"\0"), 960)
                await self.ws.send(packet)
                count += 1
                last_sent = time.monotonic_ns()
                self.emit(
                    Event(
                        "input_audio_frame_sent",
                        {
                            "pcm_offset_samples": (count - 1) * 960,
                            "samples": len(pcm) // 2,
                            "sample_rate": 16000,
                            "stream": "input",
                            "is_speech": True,
                            "listen_turn_id": self.turn_sequence,
                        },
                        last_sent,
                    )
                )
                if count == 1:
                    self.emit(
                        Event(
                            "first_audio_sent",
                            {"listen_turn_id": self.turn_sequence},
                            last_sent,
                        )
                    )
                if self.diagnostics == "frame":
                    self.emit(
                        Event(
                            "audio_frame_sent",
                            {"frame": count, "bytes": len(packet)},
                            last_sent,
                        )
                    )
            metadata = {
                "frames": count,
                "samples": samples,
                "listen_turn_id": self.turn_sequence,
            }
            self.emit(Event("audio_send_completed", metadata, last_sent))
            await asyncio.sleep(max(0, started + samples / 16000 - time.monotonic()))
            await self._send_json({"type": "listen", "state": "stop"})
            self.emit(Event("listen_stop_sent", metadata))
            return metadata

    async def _send_vad_audio(self, path, settings, output):
        from .vad_input import stream_microphone

        if output is None:
            raise ValueError("VAD input requires an explicit uplink_path")
        await self._stop_microphone()
        self.turn_sequence += 1
        await self._send_json({"type": "listen", "state": "start", "mode": "auto"})
        self.emit(
            Event(
                "listen_start_sent",
                {"listen_turn_id": self.turn_sequence, "mode": "auto"},
            )
        )
        speech_done = asyncio.get_running_loop().create_future()

        async def stream():
            try:
                await stream_microphone(
                    self.ws,
                    path,
                    settings,
                    self.turn_sequence,
                    self.emit,
                    speech_done,
                    output,
                )
            except Exception as exc:
                if not speech_done.done():
                    speech_done.set_exception(exc)
                else:
                    self.emit(Event("error", {"message": f"microphone_failed: {exc}"}))
            finally:
                if not speech_done.done():
                    speech_done.cancel()

        self.microphone_task = asyncio.create_task(stream())
        return await speech_done

    async def _stop_microphone(self):
        if self.microphone_task is not None:
            self.microphone_task.cancel()
            await asyncio.gather(self.microphone_task, return_exceptions=True)
            self.microphone_task = None

    async def abort(self):
        await self._send_json({"type": "abort", "reason": "test_interrupt"})
        self.emit(Event("abort_wire_sent"))
        await self.device.stop("interrupt")

    async def close(self):
        await self._stop_microphone()
        self.closing = True
        if (
            self.ws is not None
            and self.diagnostics_active
            and not self.diagnostics_ended.is_set()
        ):
            try:
                await self._send_json({"type": "diagnostics", "state": "finish"})
                await asyncio.wait_for(self.diagnostics_ended.wait(), 15)
            except (TimeoutError, ConnectionError, ConnectionClosed):
                # The collector marks a missing terminal batch as incomplete.
                pass
        if hasattr(self, "device"):
            await self.device.stop("connection_close")
        for task in self.mcp_tasks:
            task.cancel()
        await asyncio.gather(*self.mcp_tasks, return_exceptions=True)
        if self.ws is not None:
            await self.ws.close()
        if self.reader is not None:
            await asyncio.gather(self.reader, return_exceptions=True)
        for future in (
            getattr(self, "hello", None),
            getattr(self, "diagnostics_ready", None),
        ):
            if future is not None and future.done() and not future.cancelled():
                future.exception()
