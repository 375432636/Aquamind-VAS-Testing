import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict = field(default_factory=dict)
    at_ns: int = field(default_factory=time.monotonic_ns)
