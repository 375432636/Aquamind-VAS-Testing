"""Collect the connection's pushed events; no HTTP endpoint or token is needed."""

import json
from pathlib import Path

from .events import Event


class DiagnosticCollector:
    def __init__(self, output_dir, emit=None):
        self.path = Path(output_dir) / "vas-events.jsonl"
        self.emit = emit
        self.session_id = None
        self.cursor = 0
        self.events = []
        self.complete = True
        self.finished = False
        self.errors = []
        self.file = self.path.open("w")

    def bind_session(self, session_id):
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("diagnostics require a server session id")
        if self.session_id is not None and self.session_id != session_id:
            raise ValueError("diagnostic session does not match hello")
        self.session_id = session_id

    def accept(self, page):
        if (
            page.get("schema_version") != 1
            or page.get("server_session_id") != self.session_id
            or self.session_id is None
        ):
            self.complete = False
            self.errors.append("diagnostic_schema_or_session_mismatch")
            return
        if page.get("gap") or not page.get("complete", False):
            self.complete = False
        for event in page["events"]:
            if event.get("server_session_id") != self.session_id:
                self.complete = False
                self.errors.append("event_session_mismatch")
                continue
            if event["seq"] <= self.cursor:
                continue
            if event["seq"] != self.cursor + 1:
                self.complete = False
            self.cursor = event["seq"]
            self.events.append(event)
            self.file.write(json.dumps(event, ensure_ascii=False) + "\n")
            if self.emit:
                self.emit(Event("vas_event", event))
        self.file.flush()
        if page.get("next_seq") != self.cursor:
            self.complete = False
        self.finished = bool(
            page.get("finished") and self.cursor == page.get("end_seq")
        )

    async def finish(self):
        self.complete = self.complete and self.finished
        if not self.finished:
            self.errors.append("diagnostic_stream_ended_before_final_batch")
        self.file.close()
        return {
            "complete": self.complete,
            "finished": self.finished,
            "last_seq": self.cursor,
            "errors": self.errors,
            "path": str(self.path),
            "transport": "websocket",
        }
