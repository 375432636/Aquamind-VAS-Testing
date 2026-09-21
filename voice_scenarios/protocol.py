from pathlib import Path
from typing import Callable, Protocol

from aquamind_voice_report.events import Event

from .model import InputStream


class ConversationTransport(Protocol):
    async def connect(self, emit: Callable[[Event], None]) -> dict: ...

    async def send_audio(
        self,
        path: Path,
        *,
        input_stream: InputStream | None = None,
        uplink_path: Path | None = None,
    ) -> dict: ...

    async def abort(self) -> None: ...

    async def close(self) -> None: ...
