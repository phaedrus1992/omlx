"""The pure helpers of scripts/bench_splash_session.py."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_splash_session.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("bench_splash_session", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while the module runs.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _run(bench, arm, ttfts):
    turns = [
        bench.Turn(index, ttft, ttft * 2, input_tokens=100, cached_tokens=50)
        for index, ttft in enumerate(ttfts, start=1)
    ]
    return bench.Run(arm, 1, turns)


def test_p90_of_one_value_is_that_value(bench):
    assert bench._p90([4.0]) == 4.0


def test_p90_of_ten_values_sits_between_the_top_two(bench):
    values = [float(n) for n in range(1, 11)]

    assert 9.0 <= bench._p90(values) <= 10.0


def test_summarize_reports_an_arm_with_only_one_turn(bench):
    table = bench.summarize([_run(bench, "stock", [3.0])])

    assert "no turn after the first" in table


def test_summarize_reports_median_and_p90(bench):
    table = bench.summarize([_run(bench, "fork", [1.0, 2.0, 4.0, 8.0])])

    assert "| fork |" in table
    assert "50.0%" in table


def test_tool_use_block_with_invalid_json_raises(bench):
    blocks = bench._Blocks()
    blocks.blocks = {0: {"type": "tool_use", "name": "Tool0", "input": {}}}
    blocks.json_parts = {0: "{not json"}

    with pytest.raises(RuntimeError, match="invalid JSON"):
        blocks.content()
