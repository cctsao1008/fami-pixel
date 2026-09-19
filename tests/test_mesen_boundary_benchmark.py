from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


def _load_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "mesen_boundary_benchmark.py"
    spec = spec_from_file_location("mesen_boundary_benchmark_test_target", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_percentile_interpolates_sorted_sample():
    tool = _load_tool()

    values = [40.0, 10.0, 30.0, 20.0]
    assert tool.percentile(values, 0.0) == 10.0
    assert tool.percentile(values, 0.5) == 25.0
    assert tool.percentile(values, 1.0) == 40.0


@pytest.mark.parametrize(
    ("values", "q"),
    [
        ([], 0.5),
        ([1.0], -0.1),
        ([1.0], 1.1),
        ([float("nan")], 0.5),
    ],
)
def test_percentile_rejects_invalid_input(values, q):
    tool = _load_tool()
    with pytest.raises(ValueError):
        tool.percentile(values, q)


def test_summarize_samples_normalizes_batched_work_per_unit():
    tool = _load_tool()

    result = tool.summarize_samples(
        [4_000_000, 8_000_000],
        units_per_sample=4,
        unit="frame",
    )

    assert result["samples"] == 2
    assert result["total_units"] == 8
    assert result["p50_us_per_unit"] == 1500.0
    assert result["mean_us_per_unit"] == 1500.0
    assert result["throughput_per_second"] == pytest.approx(8 / 0.012)


def test_derive_comparisons_uses_p50_normalized_costs():
    tool = _load_tool()

    def row(value):
        return {"p50_us_per_unit": float(value)}

    comparisons = tool.derive_comparisons(
        {
            "step_sync_1": row(100.0),
            "step_sync_batch": row(25.0),
            "state_slot_roundtrip": row(200.0),
            "state_file_roundtrip": row(1000.0),
            "branch_8x4_file_scalar": row(8000.0),
            "branch_8x4_slot_scalar": row(4000.0),
            "branch_8x4_slot_batch": row(1000.0),
        }
    )

    assert comparisons == {
        "step_batch_speedup_per_frame": 4.0,
        "slot_roundtrip_speedup_vs_file": 5.0,
        "branch_slot_speedup_vs_file": 2.0,
        "branch_slot_batch_speedup_vs_slot_scalar": 4.0,
        "branch_slot_batch_speedup_vs_file_scalar": 8.0,
    }


def test_measure_reports_requested_unit_count(monkeypatch):
    tool = _load_tool()
    ticks = iter([0, 1_000, 10_000, 12_000, 20_000, 23_000])
    monkeypatch.setattr(tool.time, "perf_counter_ns", lambda: next(ticks))
    calls = []

    result = tool.measure(
        lambda: calls.append("x"),
        samples=3,
        warmup=1,
        units_per_sample=2,
        unit="frame",
    )

    assert calls == ["x", "x", "x", "x"]
    assert result["samples"] == 3
    assert result["total_units"] == 6
    assert result["unit"] == "frame"
