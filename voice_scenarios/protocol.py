import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .model import InputStream


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict = field(default_factory=dict)
    at_ns: int = field(default_factory=time.monotonic_ns)


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
