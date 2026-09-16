"""Generated fixtures keep complete Piper output, including the final syllable."""

import math
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from voice_scenarios import speech


def test_speech_disables_native_telemetry_before_loading_piper():
    env = dict(os.environ)
    env.pop("ORT_DISABLE_TELEMETRY", None)
    outcome = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from voice_scenarios import speech; "
            "assert os.environ.get('ORT_DISABLE_TELEMETRY') == '1'; "
            "assert 'onnxruntime' not in sys.modules",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert outcome.returncode == 0, outcome.stderr


@pytest.mark.parametrize("text", ["你是谁", "上海门店", "给我介绍防水的耳机"])
def test_piper_pronounces_last_character_without_trailing_newline(text):
    # The old stdin-based synthesizer lost the last Chinese character when
    # there was no newline. Exercise Piper's actual text frontend, offline.
    from piper.phonemize_espeak import EspeakPhonemizer

    phonemizer = EspeakPhonemizer()
    complete = phonemizer.phonemize("cmn", text)
    assert complete == phonemizer.phonemize("cmn", text + "\n")
    assert complete != phonemizer.phonemize("cmn", text[:-1])


def test_piper_keeps_multisentence_text_and_tail_while_resampling(
    tmp_path, monkeypatch
):
    from voice_scenarios import ci_run

    seen = []

    class Voice:
        def synthesize_wav(self, text, output):
            seen.append(text)
            # A distinctive last syllable at the end, not hidden by trailing silence.
            values = [0] * 4410 + [
                round(10000 * math.sin(i * 2 * math.pi * 700 / 22050))
                for i in range(2205)
            ]
            output.setparams((1, 2, 22050, 0, "NONE", ""))
            output.writeframes(struct.pack(f"<{len(values)}h", *values))

    monkeypatch.setattr(speech, "ensure_voice", lambda: tmp_path / "voice.onnx")
    monkeypatch.setattr(speech, "_load_voice", lambda path: Voice())
    output = tmp_path / "speech.wav"
    text = "第一句。如何成为会员？最后一个字：好。\n`$(echo untouched)`"
    ci_run.synthesize(text, output)
    assert seen == [text]
    with wave.open(str(output), "rb") as audio:
        assert audio.getparams()[:3] == (1, 2, 16000)
        assert abs(audio.getnframes() - 4800) <= 1
        pcm = audio.readframes(audio.getnframes())
    values = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    assert max(abs(v) for v in values[-160:]) > 5000
    assert ci_run.settings_from_env({"VAS_DEVICE_ID": "test"})["speech_engine"] == (
        "piper-tts/zh_CN-huayan-medium"
    )


def test_model_download_is_cached_and_never_receives_test_text(tmp_path, monkeypatch):
    from piper import download_voices

    monkeypatch.setenv("PIPER_DATA_DIR", str(tmp_path))
    calls = []

    def download(name, directory):
        calls.append(name)
        (directory / f"{name}.onnx").write_bytes(b"model")
        (directory / f"{name}.onnx.json").write_text("{}")

    monkeypatch.setattr(download_voices, "download_voice", download)
    assert speech.ensure_voice() == tmp_path / "zh_CN-huayan-medium.onnx"
    assert speech.ensure_voice().read_bytes() == b"model"
    assert calls == ["zh_CN-huayan-medium"]


def test_interrupted_download_does_not_poison_model_cache(tmp_path, monkeypatch):
    from piper import download_voices

    monkeypatch.setenv("PIPER_DATA_DIR", str(tmp_path))

    def download(name, directory):
        (directory / f"{name}.onnx").write_bytes(b"partial")
        raise OSError("connection interrupted")

    monkeypatch.setattr(download_voices, "download_voice", download)
    with pytest.raises(RuntimeError, match="Piper"):
        speech.ensure_voice()
    assert not list(tmp_path.glob("*.onnx*"))


def test_container_model_cache_does_not_require_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPER_DATA_DIR", str(tmp_path))
    model = tmp_path / "zh_CN-huayan-medium.onnx"
    model.write_bytes(b"cached model")
    model.with_suffix(".onnx.json").write_text("{}")

    def unavailable_home():
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(Path, "home", unavailable_home)
    assert speech.ensure_voice() == model


def test_piper_voice_loaded_once_for_repeated_turns(tmp_path, monkeypatch):
    from piper import PiperVoice

    calls = []
    voice = object()
    monkeypatch.setattr(PiperVoice, "load", lambda path: (calls.append(path), voice)[1])
    speech._load_voice.cache_clear()
    try:
        path = str(tmp_path / "voice.onnx")
        assert speech._load_voice(path) is voice
        assert speech._load_voice(path) is voice
        assert calls == [path]
    finally:
        speech._load_voice.cache_clear()


def test_failed_synthesis_keeps_existing_audio(tmp_path, monkeypatch):
    class Voice:
        def synthesize_wav(self, text, output):
            output.setparams((1, 2, 22050, 0, "NONE", ""))
            output.writeframes(b"\0\0")
            raise RuntimeError("synthesis interrupted")

    monkeypatch.setattr(speech, "ensure_voice", lambda: Path("model.onnx"))
    monkeypatch.setattr(speech, "_load_voice", lambda path: Voice())
    target = tmp_path / "speech.wav"
    target.write_bytes(b"existing recording")
    with pytest.raises(RuntimeError, match="synthesis interrupted"):
        speech.synthesize("你好", target)
    assert target.read_bytes() == b"existing recording"
