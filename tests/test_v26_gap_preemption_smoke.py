from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
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


def _enemy_radar(dx: int, *, grounded: bool, gap=None, invincible=False):
    return {
        "enemies": [
            {
                "slot": 0,
                "id": 0x06,
                "state": 0,
                "x": 1000 + int(dx),
                "y": 176,
                "dx": int(dx),
            }
        ],
        "nearest_enemy_dx": int(dx),
        "nearest_gap_dx": gap,
        "nearest_obstacle_dx": None,
        "grounded": bool(grounded),
        "invincible": bool(invincible),
        "objective_mode": "PROGRESS",
    }


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

    # Current authoritative support evidence is the normal completion gate.
    after_landing = v26._best_v26_plan(
        [],
        1464,
        16,
        303,
        {"nearest_gap_dx": None, "grounded": True, "objective_mode": "PROGRESS"},
    )
    assert v26._gap_commit_root_frame is None
    assert after_landing is None


def test_v26_restores_grounded_landing_zone_preemption_before_progress():
    v26 = _load_v26()
    v26._reset_gap_commitment()

    # V26 field run generation 86 had one Goomba at dx=150 inside V20's
    # +96..+160 landing corridor while Mario was grounded, yet V25/V23 kept a
    # forward-model progress plan. V26 must restore the intended long re-arm.
    plan = v26._best_v26_plan(
        [],
        541,
        16,
        80,
        _enemy_radar(150, grounded=True),
    )

    assert plan is not None
    assert plan["candidate"] == v26.v20.CLUSTER_JUMP.name
    assert plan["worker"] == "landing-reactive"
    assert "landing-zone-escape" in plan["guard_mode"]
    assert plan["schedule"][0]["frames"] == 1
    assert plan["schedule"][1]["frames"] == 15
    assert v26.v23._latest_forward_meta["forward_model_status"] == "current-landing-preempt"


def test_v26_restores_airborne_landing_zone_preemption_before_progress():
    v26 = _load_v26()
    v26._reset_gap_commitment()

    plan = v26._best_v26_plan(
        [],
        565,
        16,
        84,
        _enemy_radar(107, grounded=False),
    )

    assert plan is not None
    assert plan["candidate"] == v26.v20.CLUSTER_EXTEND.name
    assert plan["worker"] == "landing-reactive"
    assert "landing-zone-extend" in plan["guard_mode"]


def test_v26_current_gap_outranks_landing_enemy_preemption():
    v26 = _load_v26()
    v26._reset_gap_commitment()

    plan = v26._best_v26_plan(
        [],
        1405,
        16,
        300,
        _enemy_radar(120, grounded=False, gap=9),
    )

    assert plan is not None
    assert plan["candidate"] == v26.GAP_ESCAPE_CANDIDATE
    assert plan["worker"] == "current-terrain"


def test_v26_grounded_predicate_accepts_elevated_supported_surface():
    v26 = _load_v26()
    elevated_observation = SimpleNamespace(
        player_state=0,
        mario_y_high=1,
        mario_y=128,
        player_y_speed=0,
    )
    airborne_observation = SimpleNamespace(
        player_state=1,
        mario_y_high=1,
        mario_y=128,
        player_y_speed=0,
    )
    elevated_state = SimpleNamespace(
        player_state=0,
        player_y_high=1,
        player_y=128,
        player_y_speed=0,
    )

    assert v26._looks_grounded_observation(elevated_observation)
    assert not v26._looks_grounded_observation(airborne_observation)
    assert v26._grounded_from_state(elevated_state)


def test_v26_installs_support_predicate_and_preserves_reward_worker():
    v26 = _load_v26()
    v26.v25.v24._install_v24_overrides()
    v26.v25._install_v25_overrides()
    v26._install_v26_overrides()
    assert v26.v23.PLANNER_NAME == "v26-current-gap-preemption"
    assert v26.v23._best_forward_plan is v26._best_v26_plan
    assert v26.v23.shadow_worker_main is v26.v25.shadow_worker_main
    assert v26.GAP_ESCAPE_CANDIDATE in v26.v15._JUMP_NAMES
    assert v26.v16._looks_grounded is v26._looks_grounded_observation
    assert v26.v20._grounded_from_state is v26._grounded_from_state
