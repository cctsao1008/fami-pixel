#!/usr/bin/env python3
"""V35 planner: synchronous current-root Star micro-MPC.

V34 restored an eager +4f asynchronous COLLECT handoff, but field evidence showed
that no reward branch was ever admitted: the live authority advanced faster than
active shadow workers could prove continuation(+4f) + reward(+4f).  Idle workers
could publish coverage immediately, while every real +4f branch missed its own
handoff and became unreachable by construction.

For a transient Star, this is a fundamental scheduling problem rather than a
ranking problem.  V35 therefore evaluates the small eight-chunk Star vocabulary
*synchronously on the authoritative Mesen core at the current control root*:

    current live root (game paused in wall-clock time)
        -> save exact state
        -> restore/evaluate each 4f reward chunk
        -> restore exact live root
        -> commit the best safe 4f chunk
        -> reobserve/replan four frames later

Because the game does not advance while this micro-search runs, the selected
proof is rooted at the actual current authority frame.  No stale rebase, delayed
handoff, or action-lineage guess is involved.  V26 SURVIVE/gap/landing guards
remain above this lower COLLECT delegate, and V34's asynchronous machinery stays
available for non-Star/fallback operation.
"""

from __future__ import annotations

from pathlib import Path
import time

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26
import mesen_smb_checkpoint_planner_v34 as v34


PLANNER_NAME = "v35-sync-current-root-star-collect"

_LIVE_AUTHORITY_CORE = None
_SYNC_STEP_TIMEOUT = 2.0
_SYNC_CHECKPOINT = Path("build/checkpoints/v35-sync-star-current.mss")


def _sync_star_plan(core, *, current_frame: int, live_radar: dict) -> dict | None:
    """Evaluate V25's reward chunks from the exact current live Mesen root."""

    target_type = v25._collect_target_from_radar(live_radar)
    if target_type != "star":
        return None

    checkpoint = _SYNC_CHECKPOINT.expanduser().resolve()
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    root_frame, root_x, root_engine = base.save_checkpoint(core, checkpoint)
    if int(root_frame) != int(current_frame):
        # Never invent a rebase if the captured core is not exactly the selector
        # root.  Restore and let V34's normal asynchronous fallback handle it.
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        v23._latest_forward_meta = {
            "forward_model_status": "sync-star-root-mismatch",
            "objective_mode": "COLLECT",
            "collect_target_type": "star",
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
        }
        return None

    best_chunk = None
    best_outcome = None
    best_key = None
    candidate_rows: list[dict] = []

    try:
        for chunk in v25.REWARD_BEAM_CHUNKS_WITH_HOLD:
            base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
            branch_started = time.perf_counter()
            outcome = v25._evaluate_reward_chunk(
                core,
                chunk,
                target_type="star",
                request_radar=dict(live_radar or {}),
                step_timeout=float(_SYNC_STEP_TIMEOUT),
            )
            safe = not bool(outcome["died"])
            key = (
                1 if bool(outcome["collected"]) else 0,
                tuple(outcome["reward_key"]),
            )
            candidate_rows.append(
                {
                    "candidate": f"collect_{chunk.name}",
                    "safe": bool(safe),
                    "collected": bool(outcome["collected"]),
                    "reward_key": list(outcome["reward_key"]),
                    "frames": int(outcome["frames"]),
                    "compute_ms": round((time.perf_counter() - branch_started) * 1000.0, 3),
                }
            )
            if safe and (best_key is None or key > best_key):
                best_key = key
                best_chunk = chunk
                best_outcome = outcome
    finally:
        # Candidate futures are never allowed to become machine truth.
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)

    total_ms = (time.perf_counter() - started) * 1000.0
    if best_chunk is None or best_outcome is None:
        v23._latest_forward_meta = {
            "forward_model_status": "sync-star-no-safe-prefix",
            "objective_mode": "COLLECT",
            "collect_target_type": "star",
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": 0,
            "sync_collect_candidates": candidate_rows,
            "sync_collect_compute_ms": round(total_ms, 3),
        }
        return None

    current = best_outcome["observation"]
    target = best_outcome["target"]
    collected = bool(best_outcome["collected"])
    candidate = f"collect_{best_chunk.name}"
    reward_key = list(best_outcome["reward_key"])

    result = {
        # This is a current-root authority-local proof, not an asynchronous worker
        # generation.  Keeping generation=-1 prevents it from consuming unrelated
        # shadow generations in V17's response bookkeeping.
        "generation": -1,
        "worker": "authority-sync-star",
        "root_frame": int(root_frame),
        "trajectory_root_frame": int(root_frame),
        "trajectory_source_age_frames": 0,
        "age": 0,
        "planner_mode": "collect-sync-current-root",
        "target_reward_type": "star",
        "candidate": candidate,
        "schedule": v25.reward_chunk_schedule(best_chunk),
        "score": [1 if collected else 0, *reward_key],
        "progress": int(current.mario_x_abs) - int(root_x),
        "terminal": "none",
        "compute_ms": round(total_ms, 3),
        "reward_prefix_safe": True,
        "reward_collected": collected,
        "reward_key": reward_key,
        "reward_target_dx": None if target is None else int(target.get("dx", 0)),
        "reward_target_state": None if target is None else int(target.get("state", 0)),
        "reward_target_y": None if target is None else int(target.get("y", 0)),
        "reward_prefix_frames": int(best_outcome["frames"]),
        "trajectory_event": "reward_collected" if collected else "prefix_alive",
        "trajectory_safe_resolved": True,
        "trajectory_frames": int(best_outcome["frames"]),
        "trajectory_end_x": int(current.mario_x_abs),
        "trajectory_end_y": int(current.mario_y),
        "trajectory_reward_collected": collected,
        "trajectory_target_reward_type": "star",
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
        "guard_mode": (
            f"collect-sync-current-root[star,{candidate},root:{int(root_frame)},"
            f"proof:{int(best_outcome['frames'])}f]"
        ),
        "live_radar": dict(live_radar or {}),
        "sync_collect_candidates": candidate_rows,
    }

    v23._latest_forward_meta = {
        "forward_model_status": "selected-sync-current-root-star",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
        "forward_model_generation": -1,
        "forward_model_plan": candidate,
        "forward_model_event": result["trajectory_event"],
        "forward_model_source_frame": int(root_frame),
        "forward_model_source_age_frames": 0,
        "forward_model_simulated_frames": int(best_outcome["frames"]),
        "forward_model_reward_collected": collected,
        "reward_target_dx": result["reward_target_dx"],
        "sync_collect_candidates_evaluated": len(candidate_rows),
        "sync_collect_compute_ms": round(total_ms, 3),
        "sync_collect_candidates": candidate_rows,
    }
    return result


def _best_collect_or_progress(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Use exact synchronous current-root MPC for Star; retain V34 otherwise."""

    target_type = v25._collect_target_from_radar(live_radar)
    if target_type == "star" and _LIVE_AUTHORITY_CORE is not None:
        result = _sync_star_plan(
            _LIVE_AUTHORITY_CORE,
            current_frame=int(current_frame),
            live_radar=live_radar,
        )
        if result is not None:
            return result

    return v34._best_collect_or_progress(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )


def authority_main(args) -> int:
    """Capture the authority core without recording simulated branch actions."""

    global _LIVE_AUTHORITY_CORE, _SYNC_STEP_TIMEOUT, _SYNC_CHECKPOINT
    _LIVE_AUTHORITY_CORE = None
    _SYNC_STEP_TIMEOUT = float(args.step_timeout)
    _SYNC_CHECKPOINT = (
        args.checkpoint_dir.expanduser().resolve().parent / "v35-sync-star-current.mss"
    )

    original_set_controller = base.set_nes_controller_state

    def capture_authority_core(core, port, buttons):
        global _LIVE_AUTHORITY_CORE
        if int(port) == 0:
            _LIVE_AUTHORITY_CORE = core
        return original_set_controller(core, port, buttons)

    # V27 installs its action-ledger wrapper later in the authority chain.  It
    # will call this capture wrapper as its original setter, so actual authority
    # actions are still recorded exactly once.  V25's branch evaluator uses the
    # adapter setter directly, therefore simulated futures do not pollute the
    # authority ledger.
    base.set_nes_controller_state = capture_authority_core
    v11._log(
        "Planner V35: synchronous current-root Star micro-MPC enabled | "
        "pause live frames during 8x4f exact reward search; V26 SURVIVE remains higher authority"
    )
    try:
        return v34.authority_main(args)
    finally:
        base.set_nes_controller_state = original_set_controller
        _LIVE_AUTHORITY_CORE = None


def _install_v35_overrides() -> None:
    v34._install_v34_overrides()

    # Preserve V26's current-scene SURVIVE/gap/landing ordering.  Replace only
    # the lower COLLECT/PROGRESS delegate: Star is current-root synchronous,
    # everything else stays on the V34 asynchronous path.
    v26._BASE_V25_PLAN = _best_collect_or_progress

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v35_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
