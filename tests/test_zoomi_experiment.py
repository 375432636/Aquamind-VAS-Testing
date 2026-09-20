"""The local acceptance runner must prepare the chosen multi-turn input."""

import importlib.util
import json
import wave
from pathlib import Path


def test_prepare_selected_scenario_keeps_questions_and_interrupt(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "experiments/zoomi-final-chat.py"
    spec = importlib.util.spec_from_file_location("zoomi_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scenario = tmp_path / "chosen.yaml"
    scenario.write_text(
        "name: 选定的对话\n"
        "evaluation:\n"
        "  persona: Zoomi\n"
        "  knowledge_base_count: 4\n"
        "  focus: 上下文和打断\n"
        "turns:\n"
        "- {tool: 人设, text: 你是谁}\n"
        "- tool: 知识库-图文\n"
        "  text: 推荐防水耳机\n"
        "  interrupt_after_seconds: 2\n"
        "  output_kind: answer\n"
    )
    spoken = []

    def synthesize(text, output):
        spoken.append(text)
        with wave.open(str(output), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 1600)

    monkeypatch.setattr(module, "synthesize", synthesize)
    destination = tmp_path / "run"
    module.prepare(destination, scenario_path=scenario)
    assert spoken == ["你是谁", "推荐防水耳机"]
    for mode in ("manual", "vad"):
        prepared = json.loads((destination / f"{mode}.json").read_text())
        assert prepared["input"]["mode"] == mode
        assert [t["input_text"] for t in prepared["turns"]] == spoken
        assert prepared["turns"][1]["interrupt"] == {
            "after_playback_seconds": 2,
            "output_kind": "answer",
        }
        assert "选定的对话" in prepared["name"]
    # Repeated preparation shares the same WAVs instead of synthesizing again.
    module.prepare(destination, scenario_path=scenario)
    assert len(spoken) == 2
