"""Local Piper speech fixtures shared by inline, batch and mock runs."""

import argparse
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

# Disable the native uploader before ONNX Runtime is imported. Calling its
# disable_telemetry_events API later still starts an initialization uploader
# that can crash at shutdown on macOS (ORT 1.30).
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

VOICE_NAME = "zh_CN-huayan-medium"
SPEECH_ENGINE = f"piper-tts/{VOICE_NAME}"


def ensure_voice():
    """Download public model assets once; synthesis text never leaves the host."""
    configured = os.environ.get("PIPER_DATA_DIR")
    directory = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".cache/aquamind-vas-testing/piper"
    )
    names = [f"{VOICE_NAME}.onnx", f"{VOICE_NAME}.onnx.json"]
    if all(
        (directory / name).is_file() and (directory / name).stat().st_size
        for name in names
    ):
        return directory / names[0]
    from piper.download_voices import download_voice

    directory.mkdir(parents=True, exist_ok=True)
    logging.info("Preparing Piper voice %s in %s", VOICE_NAME, directory)
    # A failed transfer must not leave a nonempty partial model in the cache.
    try:
        with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as staging:
            download_voice(VOICE_NAME, Path(staging))
            for name in names:
                path = Path(staging) / name
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError(f"Missing Piper model asset: {name}")
            for name in names:
                (Path(staging) / name).replace(directory / name)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Piper voice download failed; retry python -m voice_scenarios.speech --download"
        ) from exc
    return directory / names[0]


@lru_cache(maxsize=2)
def _load_voice(path):
    from piper import PiperVoice

    return PiperVoice.load(path)


def synthesize(text, target):
    """Keep every sentence/sample, then resample to the VAS input format."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Install ffmpeg before generating Piper speech")
    voice = _load_voice(str(ensure_voice()))
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".vas-speech-", dir=target.parent
    ) as temporary:
        intermediate = Path(temporary) / "piper.wav"
        converted = Path(temporary) / "input.wav"
        with wave.open(str(intermediate), "wb") as output:
            voice.synthesize_wav(text, output)
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(intermediate),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(converted),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        with wave.open(str(converted), "rb") as output:
            if not output.getnframes():
                raise ValueError("Piper produced empty audio")
        converted.replace(target)


def main():
    parser = argparse.ArgumentParser(description="Prepare local Piper speech fixtures")
    parser.add_argument("--download", action="store_true", required=True)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.info("Piper voice ready: %s", ensure_voice())


if __name__ == "__main__":
    main()
