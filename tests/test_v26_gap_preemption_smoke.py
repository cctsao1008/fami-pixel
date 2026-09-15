from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v26():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v26.py"
        spec = spec_from_file_location("fami_pixel_v26_gap_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        return module
    finally:
        try:
            sys.path.remove(str(examples))
        except ValueError:
            pass


def test_v26_field_regression_gap9_airborne_preempts_async_progress():
    v26 = _load_v26()
    radar = {
        "nearest_gap_dx": 9,
        "grounded": False,
        "objective_mode": "PROGRESS",
    }
    plan = v26._best_v26_plan([], 1405, 16, 300, radar)
    assert plan["candidate"] == v26.GAP_EXTEND_CANDIDATE
    assert plan["worker"] == "current-terrain"
    assert plan["terrain_gap_dx"] == 9
    assert plan["guard_mode"] == "current-gap-airborne-extend[gap:9]"


def test_v26_field_regression_gap15_airborne_still_preempts():
    v26 = _load_v26()
    radar = {
        "nearest_gap_dx": 15,
        "grounded": False,
        "objective_mode": "PROGRESS",
    }
    plan = v26._best_v26_plan([], 1409, 16, 301, radar)
    assert plan["candidate"] == v26.GAP_EXTEND_CANDIDATE
    assert plan["terrain_gap_dx"] == 15


def test_v26_installs_after_v25_without_replacing_reward_worker():
    v26 = _load_v26()
    v26.v25.v24._install_v24_overrides()
    v26.v25._install_v25_overrides()
    v26._install_v26_overrides()
    assert v26.v23.PLANNER_NAME == "v26-current-gap-preemption"
    assert v26.v23._best_forward_plan is v26._best_v26_plan
    assert v26.v23.shadow_worker_main is v26.v25.shadow_worker_main
    assert v26.GAP_EXTEND_CANDIDATE in v26.v15._JUMP_NAMES
