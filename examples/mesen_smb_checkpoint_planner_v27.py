#!/usr/bin/env python3
"""V27 experimental planner: bounded multi-chunk search with learned rank/prune.

V26 established reliable current-scene safety commitments and completed World
1-1.  Issue #32 still had one architectural gap: normal PROGRESS control was
choosing from a fixed six-plan vocabulary, while the trained tiny surrogate was
not actually reducing the exact Mesen search budget.

V27 closes that source-level gap without weakening V26 safety:

- SURVIVE gap commitments remain outermost and unchanged;
- current enemy landing-zone preemption remains above asynchronous planning;
- sticky COLLECT keeps V25's exact reward-prefix worker path;
- ordinary PROGRESS now expands a bounded depth-3 multi-chunk vocabulary;
- the tiny surrogate ranks/prunes that vocabulary to a fixed top-K frontier;
- established baseline maneuvers remain mandatory diversity anchors;
- only the selected frontier is evaluated by exact Mesen event-horizon rollouts;
- authority still executes only a 4-frame proven prefix and replans.

The learned model is therefore finally doing the job intended by #22/#32:
cheap proposal ordering before expensive emulator branches.  It is never a
terminal/safety authority and cannot remove the baseline anchor set.
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
from fami_pixel.games.smb1.trajectory_search import (
    DEFAULT_SEARCH_DEPTH,
    DEFAULT_TOP_K,
    build_search_frontier,
    shard_ranked_frontier,
)
from fami_pixel.learning.tiny_mlp import TinySurrogateMLP

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v24 as v24
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26


PLANNER_NAME = "v27-bounded-beam-rank-prune"
SEARCH_DEPTH = DEFAULT_SEARCH_DEPTH
SEARCH_TOP_K = DEFAULT_TOP_K

_MODEL_CACHE: dict[str, TinySurrogateMLP] = {}
_BASE_PARTIAL_SELECTOR = v24._best_forward_plan_partial


def _surrogate_for_args(args) -> TinySurrogateMLP:
    if args.surrogate_model is None:
        raise RuntimeError("V27 progress search requires --surrogate-model")
    path = str(Path(args.surrogate_model).expanduser().resolve())
    model = _MODEL_CACHE.get(path)
    if model is None:
        model = TinySurrogateMLP.load_json(Path(path))
        _MODEL_CACHE[path] = model
    return model


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

    started = time.perf_counter()
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
    return {
        "generation": generation,
        "worker": args.worker_index,
        "root_frame": root_frame,
        "planner_mode": "progress-search",
        "candidate": best_result.plan.name,
        "schedule": v23.execution_prefix_schedule(
            best_result.plan,
            v23.EXECUTION_PREFIX_FRAMES,
        ),
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
        "surrogate_predicted_delta_x": float(best_ranked.predicted_delta_x),
        "surrogate_rank_key": list(best_ranked.rank_key[:3]),
        "surrogate_anchor": bool(best_ranked.anchored),
    }


def _best_forward_plan_partial_v27(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    result = _BASE_PARTIAL_SELECTOR(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )
    if result is None:
        return None

    keys = (
        "search_depth",
        "search_generated",
        "search_pruned",
        "search_top_k",
        "search_selected",
        "search_mesen_evaluated",
        "surrogate_predicted_delta_x",
        "surrogate_rank_key",
        "surrogate_anchor",
    )
    for key in keys:
        if key in result:
            v23._latest_forward_meta[key] = result[key]
    v23._latest_forward_meta["forward_model_status"] = "selected-ranked-beam"
    v23._latest_forward_meta["objective_mode"] = "PROGRESS"
    return result


def _v27_schedule_label(candidate_name: str) -> str:
    if candidate_name.startswith("beam_"):
        readable = candidate_name[len("beam_") :].replace("__", " -> ")
        return (
            f"BEAM {readable} | execute {v23.EXECUTION_PREFIX_FRAMES}f then replan"
        )
    return v26._v26_schedule_label(candidate_name)


def authority_main(args) -> int:
    v23.v11._log(
        "Planner V27: bounded multi-chunk PROGRESS search enabled | "
        f"depth={SEARCH_DEPTH} top-k={SEARCH_TOP_K} surrogate rank/prune -> exact Mesen; "
        "V26 SURVIVE + V25 COLLECT remain higher authority"
    )
    return v26.authority_main(args)


def _install_v27_overrides() -> None:
    # Install the validated V24/V25/V26 layers first. Then replace only the
    # ordinary PROGRESS candidate source and its telemetry/labels.
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
    # whitelist. If a generic current-scene emergency is active, an unfamiliar
    # learned-ranked prefix may be conservatively preempted rather than suppress
    # the safety layer.


def main() -> int:
    _install_v27_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
