import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
    audio: Path
    interrupt: Interruption | None = None
    expect: dict = field(default_factory=dict)
    completion_goal: str = "audio_completed"
    input_text: str | None = None


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
            path = (Path(base_dir) / value["audio"]).resolve()
            if not path.is_file():
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
                    value.get("input_text"),
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
        )

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(yaml.safe_load(path.read_text()), base_dir=path.parent)
