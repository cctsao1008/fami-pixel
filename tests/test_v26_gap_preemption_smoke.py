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


def test_v26_field_regression_gap9_airborne_starts_rearm_commitment():
    v26 = _load_v26()
    v26._reset_gap_commitment()
    radar = {
        "nearest_gap_dx": 9,
        "grounded": False,
        "objective_mode": "PROGRESS",
    }
    plan = v26._best_v26_plan([], 1405, 16, 300, radar)
    assert plan["candidate"] == v26.GAP_ESCAPE_CANDIDATE
    assert plan["worker"] == "current-terrain"
    assert plan["root_frame"] == 1405
    assert plan["age"] == 0
    assert plan["terrain_gap_trigger_dx"] == 9
    assert plan["terrain_guard"] == "airborne-rearm-commit"
    assert plan["guard_mode"].startswith("current-gap-rearm-commit[")


def test_v26_does_not_restart_rearm_each_control_quantum():
    v26 = _load_v26()
    v26._reset_gap_commitment()

    first = v26._best_v26_plan(
        [],
        1405,
        16,
        300,
        {"nearest_gap_dx": 9, "grounded": False, "objective_mode": "PROGRESS"},
    )
    second = v26._best_v26_plan(
        [],
        1409,
        16,
        301,
        {"nearest_gap_dx": 15, "grounded": False, "objective_mode": "PROGRESS"},
    )

    assert first["root_frame"] == 1405
    assert second["root_frame"] == 1405
    assert second["age"] == 4
    assert second["candidate"] == v26.GAP_ESCAPE_CANDIDATE


def test_v26_commitment_survives_gap_radar_dropout_until_landing():
    v26 = _load_v26()
    v26._reset_gap_commitment()
    v26._best_v26_plan(
        [],
        1405,
        16,
        300,
        {"nearest_gap_dx": 9, "grounded": False, "objective_mode": "PROGRESS"},
    )

    # gap=None is UNKNOWN, not proof that the crossing is complete.
    still_committed = v26._best_v26_plan(
        [],
        1413,
        16,
        302,
        {"nearest_gap_dx": None, "grounded": False, "objective_mode": "PROGRESS"},
    )
    assert still_committed["candidate"] == v26.GAP_ESCAPE_CANDIDATE
    assert still_committed["root_frame"] == 1405
    assert still_committed["age"] == 8

    # Current authoritative grounded evidence is the only normal completion gate.
    after_landing = v26._best_v26_plan(
        [],
        1464,
        16,
        303,
        {"nearest_gap_dx": None, "grounded": True, "objective_mode": "PROGRESS"},
    )
    assert v26._gap_commit_root_frame is None
    assert after_landing is None


def test_v26_installs_after_v25_without_replacing_reward_worker():
    v26 = _load_v26()
    v26.v25.v24._install_v24_overrides()
    v26.v25._install_v25_overrides()
    v26._install_v26_overrides()
    assert v26.v23.PLANNER_NAME == "v26-current-gap-preemption"
    assert v26.v23._best_forward_plan is v26._best_v26_plan
    assert v26.v23.shadow_worker_main is v26.v25.shadow_worker_main
    assert v26.GAP_ESCAPE_CANDIDATE in v26.v15._JUMP_NAMES
