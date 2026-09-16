#!/usr/bin/env python3
"""V27 experimental planner: bounded multi-chunk search with learned rank/prune.

V26 established reliable current-scene safety commitments and completed World
1-1. Issue #32 still had one architectural gap: normal PROGRESS control was
choosing from a fixed six-plan vocabulary, while the trained tiny surrogate was
not actually reducing the exact Mesen search budget.

V27 keeps the learned model in its intended role: it proposes a small frontier,
while exact Mesen remains branch authority. Current-scene SURVIVE and COLLECT
layers remain above ordinary PROGRESS search.

The first live V27 cut exposed three asynchronous-integration defects:

1. first-finisher response latency could become policy before long/brake anchors
   from the same root arrived;
2. a 4-frame fallback could cut through the middle of a multi-chunk trajectory;
3. most importantly, a stale Mesen result was rebased to ``root=current`` and
   replayed from age zero even though the proof belonged to an older state.

The third defect changes the delayed-result contract.  A historical branch is
usable only when the authoritative NES actions actually executed since its root
match that candidate's exact prefix, and when enough simulated horizon remains
to cover the next live commitment.  V27 therefore records final authority
buttons, transports branch-level worker proofs, filters by action lineage and
proof lease *before* comparing scores, and preserves the original proof root and
phase when a stale branch remains reachable.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.games.smb1 import (
    TrajectoryEvent,
    evaluate_mesen_trajectory,
    observation_from_state,
    read_smb1_state,
    trajectory_outcome_key,
)
from fami_pixel.games.smb1.action_lineage import (
    AuthorityActionLedger,
    validate_branch_proof,
)
from fami_pixel.games.smb1.actions import action_to_nes_buttons
from fami_pixel.games.smb1.forward_model import execution_prefix_schedule
from fami_pixel.games.smb1.trajectory_search import (
    DEFAULT_SEARCH_DEPTH,
    DEFAULT_TOP_K,
    build_search_frontier,
    shard_ranked_frontier,
)
from fami_pixel.learning.tiny_mlp import TinySurrogateMLP

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v24 as v24
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26


PLANNER_NAME = "v27-bounded-beam-rank-prune"
SEARCH_DEPTH = DEFAULT_SEARCH_DEPTH
SEARCH_TOP_K = DEFAULT_TOP_K
REQUIRED_PROGRESS_ANCHORS = frozenset({"fm_long_jump", "fm_brake_jump"})

_MODEL_CACHE: dict[str, TinySurrogateMLP] = {}
_PROGRESS_RESPONSE_CACHE: dict[tuple[int, int, str], dict] = {}
_AUTHORITY_ACTION_LEDGER = AuthorityActionLedger(max_entries=512)


def _surrogate_for_args(args) -> TinySurrogateMLP:
    if args.surrogate_model is None:
        raise RuntimeError("V27 progress search requires --surrogate-model")
    path = str(Path(args.surrogate_model).expanduser().resolve())
    model = _MODEL_CACHE.get(path)
    if model is None:
        model = TinySurrogateMLP.load_json(Path(path))
        _MODEL_CACHE[path] = model
    return model


def _progress_execution_schedule(plan) -> list[dict[str, int]]:
    """Keep the full evaluated trajectory as fallback between 4-frame replans."""

    schedule = execution_prefix_schedule(
        plan,
        plan.prefix_frames,
        append_release_tail=False,
    )
    schedule.append(
        {
            "buttons": int(action_to_nes_buttons(plan.tail_action)),
            "frames": 1,
        }
    )
    return schedule


def _branch_proof_payload(
    *,
    ranked,
    result,
    generation: int,
    worker: int,
    root_frame: int,
    frontier,
    compute_ms: float,
) -> dict:
    event = result.event
    candidate = result.plan.name
    return {
        "generation": int(generation),
        "worker": int(worker),
        "root_frame": int(root_frame),
        "planner_mode": "progress-search",
        "candidate": candidate,
        "schedule": _progress_execution_schedule(result.plan),
        "score": list(trajectory_outcome_key(result)),
        "progress": int(result.progress),
        "terminal": "death" if event == TrajectoryEvent.DEATH else "none",
        "compute_ms": round(float(compute_ms), 3),
        "trajectory_event": event.value,
        "trajectory_safe_resolved": bool(v23.result_is_safe_resolved(result)),
        "trajectory_frames": int(result.frames_simulated),
        "trajectory_end_x": int(result.end_x),
        "trajectory_max_x": int(result.max_x),
        "trajectory_end_y": int(result.end_y),
        "trajectory_landed": bool(result.landed),
        "trajectory_died": bool(result.died),
        "trajectory_reward_collected": bool(result.reward_collected),
        "trajectory_target_reward_type": result.target_reward_type,
        "trajectory_target_approach": result.target_approach,
        "risk_probability": float(ranked.risk_probability),
        "no_progress_probability": float(ranked.no_progress_probability),
        "search_depth": int(frontier.depth),
        "search_generated": int(frontier.generated),
        "search_pruned": int(frontier.pruned),
        "search_top_k": int(frontier.top_k),
        "search_selected": len(frontier.ranked),
        "search_mesen_evaluated": 1,
        "search_evaluated_candidates": [candidate],
        "search_evaluated_anchors": [candidate] if candidate in REQUIRED_PROGRESS_ANCHORS else [],
        "surrogate_predicted_delta_x": float(ranked.predicted_delta_x),
        "surrogate_rank_key": list(ranked.rank_key[:3]),
        "surrogate_anchor": bool(ranked.anchored),
    }


def _search_baseline_payload(
    core,
    args,
    req: dict,
    *,
    generation: int,
    root_frame: int,
    root_x: int,
    root_engine: int,
):
    """Evaluate the surrogate-pruned PROGRESS frontier and retain every proof."""

    started = time.perf_counter()
    checkpoint = Path(req["checkpoint"])
    base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
    start = observation_from_state(core.frame_count(), read_smb1_state(core))
    model = _surrogate_for_args(args)
    frontier = build_search_frontier(
        start,
        model,
        depth=SEARCH_DEPTH,
        top_k=SEARCH_TOP_K,
    )
    shard = shard_ranked_frontier(
        frontier,
        int(args.worker_index),
        int(args.worker_count),
    )

    branch_proofs: list[dict] = []
    best_result = None
    best_ranked = None
    best_proof = None
    for ranked in shard:
        plan = ranked.plan
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        branch_start = observation_from_state(core.frame_count(), read_smb1_state(core))
        branch_started = time.perf_counter()
        result = evaluate_mesen_trajectory(
            core,
            plan,
            max_horizon_frames=v23.LIVE_TRAJECTORY_HORIZON,
            step_timeout_s=args.step_timeout,
            target_reward_type=None,
            start_observation=branch_start,
            stop_on_landing=True,
        )
        proof = _branch_proof_payload(
            ranked=ranked,
            result=result,
            generation=generation,
            worker=int(args.worker_index),
            root_frame=root_frame,
            frontier=frontier,
            compute_ms=(time.perf_counter() - branch_started) * 1000.0,
        )
        branch_proofs.append(proof)
        if best_result is None or trajectory_outcome_key(result) > trajectory_outcome_key(best_result):
            best_result = result
            best_ranked = ranked
            best_proof = proof

    assert best_result is not None and best_ranked is not None and best_proof is not None
    payload = dict(best_proof)
    payload["compute_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    payload["search_mesen_evaluated"] = len(branch_proofs)
    payload["search_evaluated_candidates"] = [item["candidate"] for item in branch_proofs]
    payload["search_evaluated_anchors"] = [
        item["candidate"]
        for item in branch_proofs
        if item["candidate"] in REQUIRED_PROGRESS_ANCHORS
    ]
    payload["branch_proofs"] = branch_proofs
    return payload


def _response_branch_proofs(response: dict) -> list[dict]:
    raw = response.get("branch_proofs")
    if isinstance(raw, list) and raw:
        proofs: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            proof = dict(item)
            for key in ("generation", "worker", "root_frame", "planner_mode"):
                proof.setdefault(key, response.get(key))
            proofs.append(proof)
        if proofs:
            return proofs
    return [dict(response)]


def _cache_progress_responses(
    response_paths,
    *,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
) -> None:
    for path in response_paths:
        response = v11._read_json(path)
        if response is None or response.get("planner_mode") != "progress-search":
            continue
        for proof in _response_branch_proofs(response):
            try:
                generation = int(proof.get("generation", -1))
                root_frame = int(proof.get("root_frame", -1))
                candidate = str(proof.get("candidate") or "")
            except (TypeError, ValueError):
                continue
            if generation < 0 or root_frame < 0 or not candidate:
                continue
            _PROGRESS_RESPONSE_CACHE[(generation, root_frame, candidate)] = dict(proof)

    retention = max(int(freshness), int(v23.LIVE_TRAJECTORY_HORIZON))
    for key in list(_PROGRESS_RESPONSE_CACHE):
        generation, root_frame, _candidate = key
        age = int(current_frame) - int(root_frame)
        if (
            generation <= int(last_applied_generation)
            or age < 0
            or age > retention
        ):
            del _PROGRESS_RESPONSE_CACHE[key]


def _progress_groups() -> dict[tuple[int, int], list[dict]]:
    groups: dict[tuple[int, int], list[dict]] = {}
    for (generation, root_frame, _candidate), proof in _PROGRESS_RESPONSE_CACHE.items():
        groups.setdefault((generation, root_frame), []).append(dict(proof))
    return groups


def _evaluated_anchor_set(proofs: list[dict]) -> set[str]:
    anchors: set[str] = set()
    for proof in proofs:
        candidate = str(proof.get("candidate") or "")
        if candidate in REQUIRED_PROGRESS_ANCHORS:
            anchors.add(candidate)
        for value in proof.get("search_evaluated_anchors") or ():
            if str(value) in REQUIRED_PROGRESS_ANCHORS:
                anchors.add(str(value))
    return anchors


def _lineage_valid_proofs(
    proofs: list[dict],
    *,
    current_frame: int,
) -> tuple[list[dict], dict[str, int]]:
    valid: list[dict] = []
    rejected: dict[str, int] = {}
    for proof in proofs:
        validation = validate_branch_proof(
            proof,
            ledger=_AUTHORITY_ACTION_LEDGER,
            current_frame=current_frame,
            commit_frames=v23.EXECUTION_PREFIX_FRAMES,
        )
        if not validation.valid:
            rejected[validation.reason] = rejected.get(validation.reason, 0) + 1
            continue
        candidate = dict(proof)
        candidate["trajectory_source_age_frames"] = int(validation.source_age)
        candidate["lineage_matched_frames"] = int(validation.matched_frames)
        candidate["proof_remaining_frames"] = int(validation.proof_remaining_frames)
        candidate["lineage_validation"] = validation.reason
        valid.append(candidate)
    return valid, rejected


def _best_forward_plan_partial_v27(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Select only branch proofs that remain reachable from current authority."""

    _cache_progress_responses(
        response_paths,
        current_frame=current_frame,
        freshness=freshness,
        last_applied_generation=last_applied_generation,
    )
    groups = _progress_groups()
    ordered = sorted(groups.items(), key=lambda item: (item[0][1], item[0][0]), reverse=True)

    newest_partial = None
    newest_complete_rejection = None
    for (generation, root_frame), proofs in ordered:
        anchors = _evaluated_anchor_set(proofs)
        if newest_partial is None:
            newest_partial = (generation, root_frame, proofs, anchors)
        if not REQUIRED_PROGRESS_ANCHORS.issubset(anchors):
            continue

        lineage_valid, rejected = _lineage_valid_proofs(
            proofs,
            current_frame=current_frame,
        )
        if not lineage_valid:
            if newest_complete_rejection is None:
                newest_complete_rejection = (generation, root_frame, proofs, anchors, rejected)
            continue

        selected = max(lineage_valid, key=lambda item: tuple(item.get("score") or ()))
        result = dict(selected)
        source_root_frame = int(result["root_frame"])
        source_age = int(current_frame) - source_root_frame
        result["trajectory_root_frame"] = source_root_frame
        result["trajectory_source_age_frames"] = source_age
        result["root_frame"] = source_root_frame
        result["age"] = source_age
        result["cohort_generation"] = int(generation)
        result["cohort_size"] = len(proofs)
        result["guard_mode"] = (
            "forward-model-lineage-cohort["
            f"{result.get('trajectory_event')},"
            f"src-age:{source_age}f,"
            f"lease:{result.get('proof_remaining_frames')}f,"
            f"proofs:{len(proofs)},"
            f"anchors:{','.join(sorted(anchors))}]"
        )
        result["live_radar"] = dict(live_radar or {})

        v23._latest_forward_meta = {
            "forward_model_status": "selected-lineage-cohort",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_plan": result.get("candidate"),
            "forward_model_event": result.get("trajectory_event"),
            "forward_model_source_frame": source_root_frame,
            "forward_model_source_age_frames": source_age,
            "forward_model_simulated_frames": result.get("trajectory_frames"),
            "forward_model_progress": result.get("progress"),
            "forward_model_end_x": result.get("trajectory_end_x"),
            "forward_model_end_y": result.get("trajectory_end_y"),
            "search_depth": result.get("search_depth"),
            "search_generated": result.get("search_generated"),
            "search_pruned": result.get("search_pruned"),
            "search_top_k": result.get("search_top_k"),
            "search_selected": result.get("search_selected"),
            "search_response_count": len(proofs),
            "search_lineage_valid_count": len(lineage_valid),
            "search_lineage_rejected": rejected,
            "search_required_anchors": sorted(REQUIRED_PROGRESS_ANCHORS),
            "search_evaluated_anchors": sorted(anchors),
            "lineage_matched_frames": result.get("lineage_matched_frames"),
            "proof_remaining_frames": result.get("proof_remaining_frames"),
            "surrogate_predicted_delta_x": result.get("surrogate_predicted_delta_x"),
            "surrogate_rank_key": result.get("surrogate_rank_key"),
            "surrogate_anchor": result.get("surrogate_anchor"),
        }
        return result

    if newest_partial is None:
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-ranked-cohort",
            "objective_mode": "PROGRESS",
            "search_response_count": 0,
            "search_required_anchors": sorted(REQUIRED_PROGRESS_ANCHORS),
            "search_evaluated_anchors": [],
        }
    elif newest_complete_rejection is not None:
        generation, root_frame, proofs, anchors, rejected = newest_complete_rejection
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-lineage-valid-proof",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "search_response_count": len(proofs),
            "search_required_anchors": sorted(REQUIRED_PROGRESS_ANCHORS),
            "search_evaluated_anchors": sorted(anchors),
            "search_lineage_rejected": rejected,
        }
    else:
        generation, root_frame, proofs, anchors = newest_partial
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-ranked-anchor-quorum",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "search_response_count": len(proofs),
            "search_required_anchors": sorted(REQUIRED_PROGRESS_ANCHORS),
            "search_evaluated_anchors": sorted(anchors),
        }
    return None


def _v27_schedule_label(candidate_name: str) -> str:
    if candidate_name.startswith("beam_"):
        readable = candidate_name[len("beam_") :].replace("__", " -> ")
        return (
            f"BEAM {readable} | execute {v23.EXECUTION_PREFIX_FRAMES}f then replan; "
            "full trajectory retained as fallback"
        )
    return v26._v26_schedule_label(candidate_name)


def authority_main(args) -> int:
    _PROGRESS_RESPONSE_CACHE.clear()
    _AUTHORITY_ACTION_LEDGER.clear()
    original_set_controller = base.set_nes_controller_state

    def recording_set_controller(core, port, buttons):
        if int(port) == 0:
            _AUTHORITY_ACTION_LEDGER.record(int(core.frame_count()), int(buttons))
        return original_set_controller(core, port, buttons)

    base.set_nes_controller_state = recording_set_controller
    v11._log(
        "Planner V27: bounded multi-chunk PROGRESS search enabled | "
        f"depth={SEARCH_DEPTH} top-k={SEARCH_TOP_K} surrogate rank/prune -> exact Mesen; "
        "delayed proofs require action-lineage match + remaining proof lease; "
        "V26 SURVIVE + V25 COLLECT remain higher authority"
    )
    try:
        return v26.authority_main(args)
    finally:
        base.set_nes_controller_state = original_set_controller


def _install_v27_overrides() -> None:
    v25.v24._install_v24_overrides()
    v25._install_v25_overrides()
    v26._install_v26_overrides()

    v25._baseline_payload = _search_baseline_payload
    v24._best_forward_plan_partial = _best_forward_plan_partial_v27

    v23.PLANNER_NAME = PLANNER_NAME
    v23._forward_schedule_label = _v27_schedule_label
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v27_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
