#!/usr/bin/env python3
"""V27 experimental planner: bounded multi-chunk search with learned rank/prune.

V26 established reliable current-scene safety commitments and completed World
1-1. Issue #32 still had one architectural gap: normal PROGRESS control was
choosing from a fixed six-plan vocabulary, while the trained tiny surrogate was
not actually reducing the exact Mesen search budget.

V27 keeps the learned model in its intended role: it proposes a small frontier,
while exact Mesen remains branch authority. Current-scene SURVIVE and COLLECT
layers remain above ordinary PROGRESS search.

A field/code audit after repeated V27 deaths found two integration defects in the
first V27 cut:

1. V24's partial selector accepted the first safe response from a generation.
   With one top-K candidate per worker, branch latency became an accidental
   policy: a 27-frame low-progress landing could win before the 43/49-frame
   long/brake anchors arrived, and the generation was then marked consumed.
2. V24's fallback appended the *final* plan tail immediately after the 4-frame
   execution prefix. That is valid for the old single-macro vocabulary but can
   cut through the middle of a V27 multi-chunk plan.

V27 now caches progress-search responses by generation/root, waits until the two
crossing-capable baseline anchors (long jump and brake jump) have actually been
evaluated for that root, then selects the best safe resolved result among the
responses already available for that coherent generation. A selected plan also
retains its complete explicit trajectory as fallback; authority can still replace
it every 4 frames, but a missed replan no longer substitutes an unrelated final
Tail action in the middle of a multi-chunk sequence.
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
from fami_pixel.games.smb1.actions import action_to_nes_buttons
from fami_pixel.games.smb1.forward_live import select_fresh_partial_safe_response
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
_PROGRESS_RESPONSE_CACHE: dict[tuple[int, int, int], dict] = {}


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
    """Keep the full evaluated trajectory as fallback between 4-frame replans.

    Authority still reconsiders the plan every control quantum. The extra schedule
    only matters when no replacement arrives. Unlike V24's single-macro tail
    fallback, this cannot cut a multi-chunk plan at frame 4 and jump straight to
    the final tail action.
    """

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
    """Evaluate only the surrogate-pruned PROGRESS frontier in exact Mesen."""

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

    best_result = None
    best_ranked = None
    for ranked in shard:
        plan = ranked.plan
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        branch_start = observation_from_state(core.frame_count(), read_smb1_state(core))
        result = evaluate_mesen_trajectory(
            core,
            plan,
            max_horizon_frames=v23.LIVE_TRAJECTORY_HORIZON,
            step_timeout_s=args.step_timeout,
            target_reward_type=None,
            start_observation=branch_start,
            stop_on_landing=True,
        )
        if best_result is None or trajectory_outcome_key(result) > trajectory_outcome_key(best_result):
            best_result = result
            best_ranked = ranked

    assert best_result is not None and best_ranked is not None
    event = best_result.event
    evaluated = [item.plan.name for item in shard]
    evaluated_anchors = [
        item.plan.name for item in shard if item.plan.name in REQUIRED_PROGRESS_ANCHORS
    ]
    return {
        "generation": generation,
        "worker": args.worker_index,
        "root_frame": root_frame,
        "planner_mode": "progress-search",
        "candidate": best_result.plan.name,
        "schedule": _progress_execution_schedule(best_result.plan),
        "score": list(trajectory_outcome_key(best_result)),
        "progress": int(best_result.progress),
        "terminal": "death" if event == TrajectoryEvent.DEATH else "none",
        "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "trajectory_event": event.value,
        "trajectory_safe_resolved": bool(v23.result_is_safe_resolved(best_result)),
        "trajectory_frames": int(best_result.frames_simulated),
        "trajectory_end_x": int(best_result.end_x),
        "trajectory_max_x": int(best_result.max_x),
        "trajectory_end_y": int(best_result.end_y),
        "trajectory_landed": bool(best_result.landed),
        "trajectory_died": bool(best_result.died),
        "trajectory_reward_collected": bool(best_result.reward_collected),
        "trajectory_target_reward_type": best_result.target_reward_type,
        "trajectory_target_approach": best_result.target_approach,
        "risk_probability": float(best_ranked.risk_probability),
        "no_progress_probability": float(best_ranked.no_progress_probability),
        "search_depth": int(frontier.depth),
        "search_generated": int(frontier.generated),
        "search_pruned": int(frontier.pruned),
        "search_top_k": int(frontier.top_k),
        "search_selected": len(frontier.ranked),
        "search_mesen_evaluated": len(shard),
        "search_evaluated_candidates": evaluated,
        "search_evaluated_anchors": evaluated_anchors,
        "surrogate_predicted_delta_x": float(best_ranked.predicted_delta_x),
        "surrogate_rank_key": list(best_ranked.rank_key[:3]),
        "surrogate_anchor": bool(best_ranked.anchored),
    }


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
        try:
            generation = int(response.get("generation", -1))
            root_frame = int(response.get("root_frame", -1))
            worker = int(response.get("worker", -1))
        except (TypeError, ValueError):
            continue
        if generation < 0 or root_frame < 0 or worker < 0:
            continue
        _PROGRESS_RESPONSE_CACHE[(generation, root_frame, worker)] = dict(response)

    for key, response in list(_PROGRESS_RESPONSE_CACHE.items()):
        generation, root_frame, _worker = key
        age = int(current_frame) - int(root_frame)
        if (
            generation <= int(last_applied_generation)
            or age < 0
            or age > int(freshness)
        ):
            del _PROGRESS_RESPONSE_CACHE[key]


def _progress_groups() -> dict[tuple[int, int], list[dict]]:
    groups: dict[tuple[int, int], list[dict]] = {}
    for (generation, root_frame, _worker), response in _PROGRESS_RESPONSE_CACHE.items():
        groups.setdefault((generation, root_frame), []).append(dict(response))
    return groups


def _evaluated_anchor_set(responses: list[dict]) -> set[str]:
    anchors: set[str] = set()
    for response in responses:
        for value in response.get("search_evaluated_anchors") or ():
            anchors.add(str(value))
    return anchors


def _best_forward_plan_partial_v27(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Select from the newest coherent progress cohort, not the first finisher."""

    _cache_progress_responses(
        response_paths,
        current_frame=current_frame,
        freshness=freshness,
        last_applied_generation=last_applied_generation,
    )
    groups = _progress_groups()
    ordered = sorted(groups.items(), key=lambda item: (item[0][1], item[0][0]), reverse=True)

    newest_partial = None
    for (generation, root_frame), responses in ordered:
        anchors = _evaluated_anchor_set(responses)
        if newest_partial is None:
            newest_partial = (generation, root_frame, responses, anchors)
        if not REQUIRED_PROGRESS_ANCHORS.issubset(anchors):
            continue

        selected = select_fresh_partial_safe_response(
            responses,
            current_frame=current_frame,
            freshness=freshness,
            last_applied_generation=last_applied_generation,
            prefix_frames=v23.EXECUTION_PREFIX_FRAMES,
        )
        if selected is None:
            continue

        result = dict(selected)
        source_root_frame = int(result["root_frame"])
        source_age = int(current_frame) - source_root_frame
        result["trajectory_root_frame"] = source_root_frame
        result["trajectory_source_age_frames"] = source_age
        result["root_frame"] = int(current_frame)
        result["age"] = 0
        result["cohort_generation"] = int(generation)
        result["cohort_size"] = len(responses)
        result["guard_mode"] = (
            "forward-model-ranked-cohort["
            f"{result.get('trajectory_event')},"
            f"src-age:{source_age}f,"
            f"responses:{len(responses)},"
            f"anchors:{','.join(sorted(anchors))}]"
        )
        result["live_radar"] = dict(live_radar or {})

        total_mesen = sum(int(item.get("search_mesen_evaluated", 0)) for item in responses)
        v23._latest_forward_meta = {
            "forward_model_status": "selected-ranked-cohort",
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
            "search_response_count": len(responses),
            "search_mesen_evaluated_total": total_mesen,
            "search_required_anchors": sorted(REQUIRED_PROGRESS_ANCHORS),
            "search_evaluated_anchors": sorted(anchors),
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
    else:
        generation, root_frame, responses, anchors = newest_partial
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-ranked-anchor-quorum",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "search_response_count": len(responses),
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
    v11._log(
        "Planner V27: bounded multi-chunk PROGRESS search enabled | "
        f"depth={SEARCH_DEPTH} top-k={SEARCH_TOP_K} surrogate rank/prune -> exact Mesen; "
        "cohort waits for long+brake anchor evaluation; V26 SURVIVE + V25 COLLECT remain higher authority"
    )
    return v26.authority_main(args)


def _install_v27_overrides() -> None:
    # Install the validated V24/V25/V26 layers first. Then replace only the
    # ordinary PROGRESS candidate source and its selection semantics.
    v25.v24._install_v24_overrides()
    v25._install_v25_overrides()
    v26._install_v26_overrides()

    v25._baseline_payload = _search_baseline_payload
    v24._best_forward_plan_partial = _best_forward_plan_partial_v27

    v23.PLANNER_NAME = PLANNER_NAME
    v23._forward_schedule_label = _v27_schedule_label
    v23.authority_main = authority_main
    v23.__file__ = __file__

    # Dynamic beam names are intentionally not added to the legacy jump-name
    # whitelist. A current-scene emergency may conservatively preempt them.


def main() -> int:
    _install_v27_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
