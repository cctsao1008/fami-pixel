from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "mesen_max_speed_probe.py"
    spec = spec_from_file_location("mesen_max_speed_probe_test_target", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_scripted_input_is_nontrivial_and_32_frames():
    tool = _load_tool()
    assert len(tool.SCRIPTED_INPUT) == tool.DETERMINISM_FRAMES == 32
    assert len(set(tool.SCRIPTED_INPUT)) >= 4
    assert tool.SCRIPTED_INPUT[:8] == (tool.NES_RIGHT,) * 8
    assert tool.SCRIPTED_INPUT[-8:] == (0x00,) * 8


def test_within_mode_batch_gain_uses_normalized_p50_costs():
    tool = _load_tool()
    results = {
        "step_sync_1": {"p50_us_per_unit": 12_000.0},
        "step_sync_batch": {"p50_us_per_unit": 4_000.0},
        "branch_8x4_slot_scalar": {"p50_us_per_unit": 480_000.0},
        "branch_8x4_slot_batch": {"p50_us_per_unit": 160_000.0},
    }
    assert tool._within_mode_batch_gain(results) == {
        "step_batch_speedup_per_frame": 3.0,
        "branch_batch_speedup": 3.0,
    }


def test_speedups_compares_matching_phases_only():
    tool = _load_tool()
    normal = {
        "step_sync_1": {"p50_us_per_unit": 16_000.0},
        "branch_8x4_slot_batch": {"p50_us_per_unit": 500_000.0},
        "normal_only": {"p50_us_per_unit": 1.0},
    }
    maximum = {
        "step_sync_1": {"p50_us_per_unit": 8_000.0},
        "branch_8x4_slot_batch": {"p50_us_per_unit": 125_000.0},
        "maximum_only": {"p50_us_per_unit": 1.0},
    }
    assert tool._speedups(normal, maximum) == {
        "branch_8x4_slot_batch": 4.0,
        "step_sync_1": 2.0,
    }
