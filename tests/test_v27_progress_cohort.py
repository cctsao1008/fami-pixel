import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from fami_pixel.games.smb1.actions import action_to_nes_buttons, Smb1Action
from fami_pixel.games.smb1.forward_model import BASELINE_TRAJECTORY_PLANS
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


def _baseline_plan(name: str):
    return next(plan for plan in BASELINE_TRAJECTORY_PLANS if plan.name == name)


def _proof(
    v27,
    *,
    worker: int,
    candidate: str,
    score: tuple[int, int, int, int],
    generation: int = 20,
    root_frame: int = 200,
    frames: int = 49,
    schedule=None,
):
    if schedule is None and candidate.startswith("fm_"):
        schedule = v27._progress_execution_schedule(_baseline_plan(candidate))
    if schedule is None:
        schedule = [
            {"buttons": action_to_nes_buttons(Smb1Action.RIGHT_A_B), "frames": 4},
            {"buttons": action_to_nes_buttons(Smb1Action.RIGHT_B), "frames": 1},
        ]
    return {
        "generation": generation,
        "worker": worker,
        "root_frame": root_frame,
        "planner_mode": "progress-search",
        "candidate": candidate,
        "schedule": schedule,
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
        "search_evaluated_anchors": [candidate] if candidate in v27.REQUIRED_PROGRESS_ANCHORS else [],
    }


def _response_with_branches(*branches: dict) -> dict:
    assert branches
    top = dict(max(branches, key=lambda item: tuple(item["score"])))
    top["branch_proofs"] = [dict(item) for item in branches]
    top["search_mesen_evaluated"] = len(branches)
    top["search_evaluated_candidates"] = [item["candidate"] for item in branches]
    top["search_evaluated_anchors"] = [
        item["candidate"] for item in branches if item["candidate"].startswith("fm_")
    ]
    return top


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record_schedule(v27, *, root_frame: int, current_frame: int, schedule) -> None:
    for frame in range(root_frame, current_frame):
        buttons = v27.v11._schedule_buttons(schedule, frame - root_frame)
        v27._AUTHORITY_ACTION_LEDGER.record(frame, buttons)


def _reset(v27):
    v27._PROGRESS_RESPONSE_CACHE.clear()
    v27._AUTHORITY_ACTION_LEDGER.clear()


def test_v27_does_not_consume_generation_before_required_anchor_quorum(tmp_path):
    v27 = _load_v27()
    _reset(v27)

    fast = tmp_path / "response-0.json"
    long_jump = tmp_path / "response-1.json"
    brake_jump = tmp_path / "response-2.json"
    paths = [fast, long_jump, brake_jump]

    _write(
        fast,
        _proof(
            v27,
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

    long_proof = _proof(
        v27,
        worker=1,
        candidate="fm_long_jump",
        score=(3, 0, 108, 108),
        frames=43,
    )
    brake_proof = _proof(
        v27,
        worker=2,
        candidate="fm_brake_jump",
        score=(3, 0, 119, 119),
        frames=49,
    )
    _write(long_jump, long_proof)
    _write(brake_jump, brake_proof)
    _record_schedule(
        v27,
        root_frame=200,
        current_frame=208,
        schedule=brake_proof["schedule"],
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
    assert selected["root_frame"] == 200
    assert selected["age"] == 8
    assert selected["trajectory_source_age_frames"] == 8
    assert selected["proof_remaining_frames"] == 41
    assert v27.v23._latest_forward_meta["forward_model_status"] == "selected-lineage-cohort"


def test_higher_score_unreachable_branch_loses_to_lower_score_reachable_branch(tmp_path):
    v27 = _load_v27()
    _reset(v27)

    path = tmp_path / "response-0.json"
    brake = _proof(
        v27,
        worker=0,
        candidate="fm_brake_jump",
        score=(3, 0, 119, 119),
        frames=49,
    )
    long_jump = _proof(
        v27,
        worker=0,
        candidate="fm_long_jump",
        score=(3, 0, 108, 108),
        frames=43,
    )
    _write(path, _response_with_branches(brake, long_jump))
    _record_schedule(v27, root_frame=200, current_frame=208, schedule=long_jump["schedule"])

    selected = v27._best_forward_plan_partial_v27(
        [path],
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={},
    )

    assert selected is not None
    assert selected["candidate"] == "fm_long_jump"
    assert selected["root_frame"] == 200
    assert selected["age"] == 8
    assert v27.v23._latest_forward_meta["search_lineage_valid_count"] == 1
    assert v27.v23._latest_forward_meta["search_lineage_rejected"]["lineage-mismatch"] == 1


def test_newer_partial_allows_older_complete_only_when_lineage_matches(tmp_path):
    v27 = _load_v27()
    _reset(v27)

    older_path = tmp_path / "response-old.json"
    newer_path = tmp_path / "response-new.json"
    older_brake = _proof(
        v27,
        worker=0,
        candidate="fm_brake_jump",
        score=(3, 0, 119, 119),
        generation=20,
        root_frame=200,
        frames=49,
    )
    older_long = _proof(
        v27,
        worker=0,
        candidate="fm_long_jump",
        score=(3, 0, 108, 108),
        generation=20,
        root_frame=200,
        frames=43,
    )
    newer_partial = _proof(
        v27,
        worker=1,
        candidate="fm_long_jump",
        score=(3, 0, 90, 90),
        generation=21,
        root_frame=204,
        frames=43,
    )
    _write(older_path, _response_with_branches(older_brake, older_long))
    _write(newer_path, newer_partial)
    _record_schedule(v27, root_frame=200, current_frame=208, schedule=older_brake["schedule"])

    selected = v27._best_forward_plan_partial_v27(
        [newer_path, older_path],
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={},
    )
    assert selected is not None
    assert selected["generation"] == 20
    assert selected["root_frame"] == 200
    assert selected["age"] == 8

    _reset(v27)
    for frame in range(200, 208):
        v27._AUTHORITY_ACTION_LEDGER.record(
            frame,
            action_to_nes_buttons(Smb1Action.RIGHT),
        )
    selected = v27._best_forward_plan_partial_v27(
        [newer_path, older_path],
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={},
    )
    assert selected is None
    assert v27.v23._latest_forward_meta["forward_model_status"] == "waiting-lineage-valid-proof"


def test_v27_rejects_lineage_match_when_remaining_proof_is_short(tmp_path):
    v27 = _load_v27()
    _reset(v27)

    path = tmp_path / "response-0.json"
    brake = _proof(
        v27,
        worker=0,
        candidate="fm_brake_jump",
        score=(3, 0, 119, 119),
        frames=10,
    )
    long_jump = _proof(
        v27,
        worker=0,
        candidate="fm_long_jump",
        score=(3, 0, 108, 108),
        frames=10,
    )
    _write(path, _response_with_branches(brake, long_jump))
    _record_schedule(v27, root_frame=200, current_frame=208, schedule=brake["schedule"])

    selected = v27._best_forward_plan_partial_v27(
        [path],
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={},
    )
    assert selected is None
    assert v27.v23._latest_forward_meta["forward_model_status"] == "waiting-lineage-valid-proof"
    assert v27.v23._latest_forward_meta["search_lineage_rejected"]["proof-lease-expired"] >= 1


def test_v27_multichunk_fallback_keeps_explicit_middle_commands():
    v27 = _load_v27()
    plans = generate_bounded_trajectory_plans(depth=3)
    plan = next(
        plan
        for plan in plans
        if plan.name == "beam_coast2__hold_right_jump4__rearm_long"
    )

    schedule = v27._progress_execution_schedule(plan)

    assert v27.v11._schedule_buttons(schedule, 0) == action_to_nes_buttons(Smb1Action.NOOP)
    assert v27.v11._schedule_buttons(schedule, 1) == action_to_nes_buttons(Smb1Action.NOOP)
    assert v27.v11._schedule_buttons(schedule, 2) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, 4) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, 5) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, 6) == action_to_nes_buttons(Smb1Action.RIGHT_B)
    assert v27.v11._schedule_buttons(schedule, 7) == action_to_nes_buttons(Smb1Action.RIGHT_A_B)
    assert v27.v11._schedule_buttons(schedule, plan.prefix_frames) == action_to_nes_buttons(
        Smb1Action.RIGHT_B
    )
