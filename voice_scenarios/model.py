import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SENSOR_COMMANDS = {
    "touch-head": "摸头",
    "touch-hand": "摸手",
    "shake-body": "摇晃身体",
    "throw-it-up": "抛起／跌落",
}


def sensor_command(value):
    if not isinstance(value, str) or value not in SENSOR_COMMANDS:
        raise ValueError("sensor must be one of: " + ", ".join(SENSOR_COMMANDS))
    return value


def positive_number(value, name, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError(
            f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}"
        )
    return float(value)


@dataclass(frozen=True)
class Interruption:
    after_playback_seconds: float
    ack_timeout_seconds: float = 3
    max_lateness_seconds: float = 0.25
    output_kind: str = "any"
    anchor: str = "playback_started"


@dataclass(frozen=True)
class Turn:
    id: str
    audio: Path | None = None
    interrupt: Interruption | None = None
    expect: dict = field(default_factory=dict)
    completion_goal: str = "audio_completed"
    input_text: str | None = None
    sensor: str | None = None

    def __post_init__(self):
        if (self.audio is None) == (self.sensor is None):
            raise ValueError("turn requires exactly one of audio or sensor")
        if self.sensor is not None:
            sensor_command(self.sensor)


@dataclass(frozen=True)
class InputStream:
    mode: str = "manual"
    pre_roll_seconds: float = 0.3
    noise_dbfs: float = -55
    noise_seed: int = 0

    def __post_init__(self):
        if self.mode not in {"manual", "vad"}:
            raise ValueError("input.mode must be manual or vad")
        positive_number(self.pre_roll_seconds, "pre_roll_seconds", allow_zero=True)
        if (
            isinstance(self.noise_dbfs, bool)
            or not isinstance(self.noise_dbfs, (int, float))
            or not math.isfinite(self.noise_dbfs)
            or not -90 <= self.noise_dbfs <= -10
        ):
            raise ValueError("noise_dbfs must be between -90 and -10")
        if isinstance(self.noise_seed, bool) or not isinstance(self.noise_seed, int):
            raise ValueError("noise_seed must be an integer")


@dataclass(frozen=True)
class Scenario:
    name: str
    turns: tuple[Turn, ...]
    turn_timeout_seconds: float = 30
    settle_seconds: float = 0.2
    input: InputStream = field(default_factory=InputStream)
    greeting_wait_seconds: float = 0.5
    greeting_timeout_seconds: float = 60

    @classmethod
    def from_dict(cls, data, *, base_dir=Path.cwd()):
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("turns"), list)
            or not data["turns"]
        ):
            raise ValueError("scenario requires a nonempty turns list")
        turns = []
        for index, value in enumerate(data["turns"], 1):
            if (
                not isinstance(value, dict)
                or sum(key in value for key in ("audio", "sensor")) != 1
            ):
                raise ValueError("turn requires exactly one of audio or sensor")
            sensor = sensor_command(value["sensor"]) if "sensor" in value else None
            path = (
                (Path(base_dir) / value["audio"]).resolve() if sensor is None else None
            )
            if path is not None and not path.is_file():
                raise ValueError(f"input audio does not exist: {path}")
            turn_id = str(value.get("id", f"turn-{index}"))
            if turn_id in {turn.id for turn in turns}:
                raise ValueError("turn ids must be unique")
            interruption = value.get("interrupt")
            if interruption is not None:
                interruption = Interruption(
                    (
                        positive_number(
                            interruption["after_ms"], "after_ms", allow_zero=True
                        )
                        / 1000
                        if "after_ms" in interruption
                        else positive_number(
                            interruption["after_playback_seconds"],
                            "after_playback_seconds",
                            allow_zero=True,
                        )
                    ),
                    positive_number(
                        interruption.get("ack_timeout_seconds", 3),
                        "ack_timeout_seconds",
                    ),
                    positive_number(
                        interruption.get("max_lateness_seconds", 0.25),
                        "max_lateness_seconds",
                        allow_zero=True,
                    ),
                    interruption.get(
                        "output_kind", "answer" if "after_ms" in interruption else "any"
                    ),
                    interruption.get("anchor", "playback_started"),
                )
            if interruption and (
                interruption.output_kind
                not in {"answer", "filler", "pre_speech", "any", "music"}
                or interruption.anchor
                not in {"playback_started", "music_playback_started"}
            ):
                raise ValueError("unsupported interruption anchor/output_kind")
            goal = value.get("completion_goal", "audio_completed")
            if goal not in {"audio_completed", "music_started", "music_stopped"}:
                raise ValueError("unsupported completion_goal")
            turns.append(
                Turn(
                    turn_id,
                    path,
                    interruption,
                    value.get("expect", {}),
                    goal,
                    value.get("input_text")
                    or (f"传感器 · {SENSOR_COMMANDS[sensor]}" if sensor else None),
                    sensor,
                )
            )
        return cls(
            str(data.get("name", "audio-scenario")),
            tuple(turns),
            positive_number(
                data.get("turn_timeout_seconds", 30), "turn_timeout_seconds"
            ),
            positive_number(
                data.get("settle_seconds", 0.2), "settle_seconds", allow_zero=True
            ),
            InputStream(**data.get("input", {})),
            positive_number(
                data.get("greeting_wait_seconds", 0.5),
                "greeting_wait_seconds",
                allow_zero=True,
            ),
            positive_number(
                data.get("greeting_timeout_seconds", 60), "greeting_timeout_seconds"
            ),
        )

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(yaml.safe_load(path.read_text()), base_dir=path.parent)
