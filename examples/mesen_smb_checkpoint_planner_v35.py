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
remain above this lower COLLECT delegate, and V34's asynchronous worker/search
machinery stays available for non-Star/fallback operation while objective routing,
PROGRESS fallback, async COLLECT authority scan, and the outer live-core capture
scope are now bound through stable control composition.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.control import (
    AuthorityRunResetPlan,
    AuthorityRuntimeScope,
    CollectProgressControl,
    EagerCollectControl,
    NamedRunReset,
)

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

# Freeze the concrete historical authority ancestry once at the current
# composition root.  The reset plan below mirrors the exact descent order,
# including the duplicate V32/V29 clear of the same COLLECT response cache.
_V33 = v34.v33
_V32 = _V33.v32
_V30 = _V32.v31.v30
_V29 = _V30.v29
_V28 = _V29.v28
_V27 = _V28.v27


def _proof_horizon_for_freshness(freshness: int) -> int:
    """Compatibility adapter for the historical proof-horizon implementation."""

    return int(
        v34.v30._proof_horizon(
            type("A", (), {"plan_freshness": int(freshness)})()
        )
    )


def _reset_v34_handoff_cache() -> None:
    v34._install_handoff_cache().clear()


def _reset_v33_deadline_cache() -> None:
    _V33._install_deadline_cache().clear()


def _reset_v32_collect_response_cache() -> None:
    _V29._COLLECT_RESPONSE_CACHE.clear()


def _reset_v29_collect_response_cache() -> None:
    _V29._COLLECT_RESPONSE_CACHE.clear()


def _reset_v28_authority_plan_memory() -> None:
    _V28._AUTHORITY_PLAN_MEMORY.clear()


def _reset_v27_progress_response_cache() -> None:
    _V27._PROGRESS_RESPONSE_CACHE.clear()


def _reset_v27_authority_action_ledger() -> None:
    _V27._AUTHORITY_ACTION_LEDGER.clear()


def _reset_v26_gap_commitment() -> None:
    v26._reset_gap_commitment()


# Transitional composition roots for the current lower objective path.
# Historical modules still provide concrete implementations, but the selector
# below no longer discovers those dependencies through transitive module globals.
_EAGER_COLLECT_CONTROL = EagerCollectControl(
    response_reader=v11._read_json,
    cache_provider=v34._install_handoff_cache,
    handoff_frames=tuple(v34.COLLECT_HANDOFF_FRAMES),
    active_workers=v34._active_collect_workers,
    proof_selector=v34.select_lineage_collect_proof,
    ledger=v34.v27._AUTHORITY_ACTION_LEDGER,
    proof_horizon_frames=_proof_horizon_for_freshness,
    commit_frames=v23.EXECUTION_PREFIX_FRAMES,
)
_COLLECT_PROGRESS_CONTROL = CollectProgressControl(
    target_selector=v25._collect_target_from_radar,
    progress_selector=v34.v27._best_forward_plan_partial_v27,
    eager_collect=_EAGER_COLLECT_CONTROL,
)
_AUTHORITY_RUNTIME_SCOPE = AuthorityRuntimeScope(
    get_controller_setter=lambda: base.set_nes_controller_state,
    install_controller_setter=lambda setter: setattr(
        base,
        "set_nes_controller_state",
        setter,
    ),
)
_AUTHORITY_RUN_RESET_PLAN = AuthorityRunResetPlan(
    steps=(
        NamedRunReset("v34-handoff-cache", _reset_v34_handoff_cache),
        NamedRunReset("v33-deadline-cache", _reset_v33_deadline_cache),
        NamedRunReset("v32-collect-response-cache", _reset_v32_collect_response_cache),
        NamedRunReset("v29-collect-response-cache", _reset_v29_collect_response_cache),
        NamedRunReset("v28-authority-plan-memory", _reset_v28_authority_plan_memory),
        NamedRunReset("v27-progress-response-cache", _reset_v27_progress_response_cache),
        NamedRunReset("v27-authority-action-ledger", _reset_v27_authority_action_ledger),
        NamedRunReset("v26-gap-commitment", _reset_v26_gap_commitment),
    )
)


def _sync_star_plan_untracked(core, *, current_frame: int, live_radar: dict) -> dict | None:
    """Evaluate V25's reward chunks from the exact current live Mesen root."""

    target_type = _COLLECT_PROGRESS_CONTROL.target_type(live_radar)
    if target_type != "star":
        return None

    checkpoint = _SYNC_CHECKPOINT.expanduser().resolve()
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    root_frame, root_x, root_engine = base.save_checkpoint(core, checkpoint)
    if int(root_frame) != int(current_frame):
        # Never invent a rebase if the captured core is not exactly the selector
        # root.  Restore and let the asynchronous fallback handle it.
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


def _sync_star_plan(core, *, current_frame: int, live_radar: dict) -> dict | None:
    """Run current-root Star speculation without mutating authority action history.

    V27 instruments ``base.set_nes_controller_state`` to record the final input
    used for each real ``frame -> frame+1`` transition.  Base checkpoint helpers
    intentionally write NOOP before save/load, but V35 uses those helpers while
    exploring counterfactual futures without advancing live authority.  Keep the
    whole synchronous micro-search outside the ledger so speculative save/restore
    controller writes can never masquerade as authoritative history.
    """

    ledger = _COLLECT_PROGRESS_CONTROL.eager_collect.ledger
    with ledger.suspend_recording():
        return _sync_star_plan_untracked(
            core,
            current_frame=int(current_frame),
            live_radar=live_radar,
        )


def _best_collect_or_progress(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Use synchronous Star MPC above explicit COLLECT/PROGRESS composition."""

    target_type = _COLLECT_PROGRESS_CONTROL.target_type(live_radar)
    if target_type == "star" and _LIVE_AUTHORITY_CORE is not None:
        result = _sync_star_plan(
            _LIVE_AUTHORITY_CORE,
            current_frame=int(current_frame),
            live_radar=live_radar,
        )
        if result is not None:
            return result

    decision = _COLLECT_PROGRESS_CONTROL.decide(
        response_paths,
        current_frame=current_frame,
        freshness=freshness,
        last_applied_generation=last_applied_generation,
        target_type=target_type,
        live_radar=live_radar,
    )
    if decision.meta is not None:
        v23._latest_forward_meta = decision.meta
    return decision.plan


def authority_main(args) -> int:
    """Capture the live authority core through the stable controller scope."""

    global _LIVE_AUTHORITY_CORE, _SYNC_STEP_TIMEOUT, _SYNC_CHECKPOINT
    _LIVE_AUTHORITY_CORE = None
    _SYNC_STEP_TIMEOUT = float(args.step_timeout)
    _SYNC_CHECKPOINT = (
        args.checkpoint_dir.expanduser().resolve().parent / "v35-sync-star-current.mss"
    )

    def capture_layer(original_set_controller):
        def capture_authority_core(core, port, buttons):
            global _LIVE_AUTHORITY_CORE
            if int(port) == 0:
                _LIVE_AUTHORITY_CORE = core
            return original_set_controller(core, port, buttons)

        return capture_authority_core

    # V27 installs its action-ledger wrapper later in the authority chain.  It
    # wraps the setter visible inside this stable scope, so real authority calls
    # remain recording -> capture -> base.  V35 separately suspends ledger writes
    # across synchronous speculative save/restore/search.
    v11._log(
        "Planner V35: synchronous current-root Star micro-MPC enabled | "
        "pause live frames during 8x4f exact reward search; V26 SURVIVE remains higher authority"
    )
    try:
        with _AUTHORITY_RUNTIME_SCOPE.controller_layer(capture_layer):
            return v34.authority_main(args)
    finally:
        _LIVE_AUTHORITY_CORE = None


def _install_v35_overrides() -> None:
    v34._install_v34_overrides()

    # Preserve V26's current-scene SURVIVE/gap/landing ordering. Replace only
    # the lower COLLECT/PROGRESS delegate through the stable control seam: Star
    # is current-root synchronous; lower objective routing is stable control.
    v26.install_lower_plan_delegate(_best_collect_or_progress)

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v35_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
