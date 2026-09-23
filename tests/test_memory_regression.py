from pathlib import Path

import pytest
import yaml

from voice_scenarios.report import evaluate


def test_repeated_question_is_in_consecutive_turns():
    root = Path(__file__).parents[1]
    scenario = yaml.safe_load((root / "config/regression.example.yaml").read_text())
    script = yaml.safe_load((root / "config/fake-regression.yaml").read_text())
    index = next(
        i
        for i, t in enumerate(scenario["turns"])
        if t["id"] == "repeated-question-new-turn"
    )
    # A different intervening query would hide a broken query-only cache key.
    assert script["turns"][index]["text"] == script["turns"][index - 1]["text"]


@pytest.mark.parametrize(
    "requests, passed", [(0, False), (1, True), (2, False), (3, False)]
)
def test_tool_loop_scenario_requires_one_memory_read(requests, passed):
    """Exercise the shipped scenario through the actual verdict evaluator."""
    scenario = yaml.safe_load(
        (Path(__file__).parents[1] / "config/regression.example.yaml").read_text()
    )
    expected = scenario["turns"][1]["expect"]
    expected = {
        key: value for key, value in expected.items() if key.startswith("memory_")
    }
    events = []
    for i in range(requests):
        for offset, edge in enumerate(("started", "finished")):
            events.append(
                dict(
                    event=f"memory_request_{edge}",
                    listen_turn_id=1,
                    span_id=f"memory-{i}",
                    clock_id="vas",
                    monotonic_ns=i * 10 + offset,
                    status="ok",
                    data={"operation": "get_memory"},
                )
            )
    report = evaluate(
        dict(
            name="tool-loop-memory",
            turns=[
                dict(
                    id="one",
                    listen_turn_id=1,
                    status="completed",
                    events=[],
                    expected=expected,
                )
            ],
        ),
        events,
    )
    assert len(report["turns"][0]["checks"]) == 1
    assert report["turns"][0]["checks"][0]["passed"] is passed
