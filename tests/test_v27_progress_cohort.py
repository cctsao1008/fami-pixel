import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from fami_pixel.games.smb1.actions import action_to_nes_buttons, Smb1Action
from fami_pixel.games.smb1.trajectory_search import generate_bounded_trajectory_plans


def _load_v27():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v27.py"
        spec = spec_from_file_location("planner_v27_progress_cohort", path)
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


def _response(
    *,
    worker: int,
    candidate: str,
    score: tuple[int, int, int, int],
    anchors=(),
    generation: int = 20,
    root_frame: int = 200,
    frames: int = 27,
):
    return {
        "generation": generation,
        "worker": worker,
        "root_frame": root_frame,
        "planner_mode": "progress-search",
        "candidate": candidate,
        "schedule": [{"buttons": 0x82, "frames": 4}],
        "score": list(score),
        "progress": score[-1],
        "terminal": "none",
        "trajectory_event": "landed",
        "trajectory_safe_resolved": True,
        "trajectory_frames": frames,
        "trajectory_end_x": 1000 + score[-1],
        "trajectory_end_y": 176,
        "search_depth": 3,
        "search_generated": 375,
        "search_pruned": 363,
        "search_top_k": 12,
        "search_selected": 12,
        "search_mesen_evaluated": 1,
        "search_evaluated_candidates": [candidate],
        "search_evaluated_anchors": list(anchors),
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v27_does_not_consume_generation_before_required_anchor_quorum(tmp_path):
    v27 = _load_v27()
    v27._PROGRESS_RESPONSE_CACHE.clear()

    fast = tmp_path / "response-0.json"
    long_jump = tmp_path / "response-1.json"
    brake_jump = tmp_path / "response-2.json"
    paths = [fast, long_jump, brake_jump]

    # Reproduce the live race exposed by the deterministic pit probe: a learned
    # beam proves a short +68 landing earlier than the slower +108/+119 anchors.
    _write(
        fast,
        _response(
            worker=0,
            candidate="beam_hold_right_jump4__rearm_short__rearm_long",
            score=(3, 0, 68, 68),
            frames=27,
        ),
    )

    selected = v27._best_forward_plan_partial_v27(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={},
    )
    assert selected is None
    assert v27.v23._latest_forward_meta["forward_model_status"] == "waiting-ranked-anchor-quorum"

    # Once both crossing-capable anchors from the same root have been evaluated,
    # selection may proceed and must compare their exact Mesen scores instead of
    # keeping the first finisher.
    _write(
        long_jump,
        _response(
            worker=1,
            candidate="fm_long_jump",
            score=(3, 0, 108, 108),
            anchors=("fm_long_jump",),
            frames=43,
        ),
    )
    _write(
        brake_jump,
        _response(
            worker=2,
            candidate="fm_brake_jump",
            score=(3, 0, 119, 119),
            anchors=("fm_brake_jump",),
            frames=49,
        ),
    )

    selected = v27._best_forward_plan_partial_v27(
        paths,
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={},
    )
    assert selected is not None
    assert selected["candidate"] == "fm_brake_jump"
    assert tuple(selected["score"]) == (3, 0, 119, 119)
    assert v27.v23._latest_forward_meta["forward_model_status"] == "selected-ranked-cohort"
    assert set(v27.v23._latest_forward_meta["search_evaluated_anchors"]) == {
        "fm_long_jump",
        "fm_brake_jump",
    }


def test_v27_multichunk_fallback_keeps_explicit_middle_commands():
    v27 = _load_v27()
    plans = generate_bounded_trajectory_plans(depth=3)
    plan = next(
        plan
        for plan in plans
        if plan.name == "beam_coast2__hold_right_jump4__rearm_long"
    )

    schedule = v27._progress_execution_schedule(plan)

    # Expected explicit plan:
    #   NOOP 2f -> RIGHT+A+B 4f -> RIGHT+B 1f -> RIGHT+A+B 15f -> tail RIGHT+B
    assert v27.v11._schedule_buttons(schedule, 0) == action_to_nes_buttons(Smb1Action.NOOP)
    assert v27.v11._schedule_buttons(schedule, 1) == action_to_nes_buttons(Smb1Action.NOOP)
    assert v27.v11._schedule_buttons(schedule, 2) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, 4) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, 5) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, 6) == action_to_nes_buttons(Smb1Action.RIGHT_B)
    assert v27.v11._schedule_buttons(schedule, 7) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)

    # After the explicit prefix is exhausted, V11 may hold only the declared
    # final tail. It must not jump to that tail at the 4-frame replan boundary.
    assert v27.v11._schedule_buttons(schedule, plan.prefix_frames) == action_to_nes_buttons(
        Smb1Action.RIGHT_B
    )
