#!/usr/bin/env python3
"""V35 planner: synchronous current-root Star micro-MPC.

V34 restored an eager +4f asynchronous COLLECT handoff, but field evidence showed
that no reward branch was ever admitted: the live authority advanced faster than
active shadow workers could prove continuation(+4f) + reward(+4f). Idle workers
could publish coverage immediately, while every real +4f branch missed its own
handoff and became unreachable by construction.

For a transient Star, this is a fundamental scheduling problem rather than a
ranking problem. V35 therefore evaluates the small eight-chunk Star vocabulary
*synchronously on the authoritative Mesen core at the current control root*:

    current live root (game paused in wall-clock time)
        -> save exact state
        -> restore/evaluate each 4f reward chunk
        -> restore exact live root
        -> commit the best safe 4f chunk
        -> reobserve/replan four frames later

Because the game does not advance while this micro-search runs, the selected
proof is rooted at the actual current authority frame. No stale rebase, delayed
handoff, or action-lineage guess is involved. V26 SURVIVE/gap/landing guards
remain above this lower COLLECT delegate, and V34's asynchronous worker/search
machinery stays available for non-Star/fallback operation while objective routing,
PROGRESS fallback, async COLLECT authority scan, controller instrumentation,
V34/V33/V32/V29/V28/V27/V26/V25 run-start resets, V30 proof-horizon setup, V28
checkpoint request enrichment, V27 authority-action lineage recording, V23
bootstrap/setup, and the live per-frame authority loop are now bound through
stable control composition. Historical V26/V25/V24/V23/V17 authority wrappers
remain available for provenance, but the active path enters ``LiveAuthorityControl``
directly. V26's SURVIVE policy remains active in its selector.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.control import (
    AuthorityContinuationRequestEnricher,
    AuthorityRunResetPlan,
    AuthorityRunSetupPlan,
    AuthorityRuntimeScope,
    CollectProgressControl,
    EagerCollectControl,
    LiveAuthorityControl,
    NamedRunReset,
    NamedRunSetup,
    authority_action_recording_layer,
    installed_checkpoint_request_enricher,
)
from fami_pixel.planning import collect_target_from_radar

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
# composition root. The reset plan below mirrors the exact descent order,
# including the duplicate V32/V29 clear of the same COLLECT response cache.
_V33 = v34.v33
_V32 = _V33.v32
_V30 = _V32.v31.v30
_V29 = _V30.v29
_V28 = _V29.v28
_V27 = _V28.v27
_V24 = v25.v24
_V17 = v23.v17


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


def _reset_v25_live_objective() -> None:
    v25._LIVE_OBJECTIVE.clear()


def _setup_v30_collect_runtime(args) -> int:
    """Install V30's run-scoped proof horizon before entering lower authority."""

    proof_horizon = int(_V30._proof_horizon(args))
    _V28.COLLECT_PROOF_HORIZON = proof_horizon
    budget = _V30.CollectTreeBudget(
        chunk_count=len(v25.REWARD_BEAM_CHUNKS_WITH_HOLD),
        handoffs=tuple(int(value) for value in _V28.COLLECT_HANDOFF_FRAMES),
        proof_horizon=proof_horizon,
    )
    v11._log(
        "Planner V30: shared-prefix delayed COLLECT enabled | "
        f"proof-horizon={proof_horizon}f handoffs={tuple(_V28.COLLECT_HANDOFF_FRAMES)} "
        f"budget naive={budget.naive_exact_steps}f shared={budget.shared_exact_steps}f "
        f"saved={budget.saved_exact_steps}f"
    )
    return proof_horizon


def _setup_v23_process_job(_args):
    """Preserve V23's optional Windows supervisor-job attachment."""

    if v23.os.name != "nt":
        return None
    try:
        joined = bool(v23.join_windows_job_from_env())
        if joined:
            v11._log("Process job : authority joined supervisor kill-on-close job")
        else:
            v11._log("Process job : no supervisor job supplied; using Python cleanup only")
        return joined
    except OSError as exc:
        v11._log(f"Process job : join failed ({exc}); using fallback cleanup")
        return False


def _setup_v23_surrogate_model(args) -> Path:
    """Validate and normalize the legacy surrogate artifact exactly as V23 did."""

    if args.surrogate_model is None:
        raise SystemExit(
            "V23 requires --surrogate-model <trained JSON artifact> for legacy telemetry/fallback compatibility"
        )
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path
    return model_path


def _setup_v23_runtime_dir(args) -> Path:
    """Create V23's isolated authority/worker IPC directory and remap paths."""

    runtime_dir = v23.isolated_run_dir(args.checkpoint_dir)
    args.checkpoint_dir = runtime_dir
    args.shadow_home = args.shadow_home.expanduser().resolve() / runtime_dir.name
    return runtime_dir


def _setup_v23_live_stack(_args) -> None:
    """Install V20 observation/watchdog/evidence then V23 forward-model hooks."""

    v23.v20._install_landing_overrides()
    v23._install_forward_overrides()


def _prepare_v17_live_run(args) -> Path:
    """Preserve V17's run-local model/risk/cache setup before the stable loop."""

    if args.surrogate_model is None:
        raise SystemExit("V17 requires --surrogate-model <trained JSON artifact>")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    _V17.v14._RISK_CUTOFF = float(args.surrogate_risk_cutoff)
    _V17.v13._RISK_PENALTY = float(args.surrogate_risk_penalty)
    _V17.v13._STALL_PENALTY = float(args.surrogate_no_progress_penalty)
    _V17.v13._DX_WEIGHT = float(args.surrogate_dx_weight)
    _V17.v12.reset_response_cache()
    return model_path


def _build_live_authority_control() -> LiveAuthorityControl:
    """Bind the installed V17-era providers to the stable authority loop.

    This factory is intentionally called *after* V23/V20 setup. Those historical
    installers still provide the current concrete watchdog/radar/evidence/worker
    functions, but they no longer own the live per-frame control loop itself.
    """

    return LiveAuthorityControl(
        prepare_run=_prepare_v17_live_run,
        create_recorder=_V17._create_recorder,
        spawn_workers=_V17.v15._spawn_shadow_workers,
        core_factory=_V17.MesenCore,
        configure_controller=_V17.configure_standard_nes_controller,
        enter_world=base.enter_world_1_1,
        observe_state=lambda core, state: _V17.observation_from_state(
            core.frame_count(), state
        ),
        read_state=_V17.read_smb1_state,
        derive_events=_V17.derive_game_events,
        set_controller_state=base.set_nes_controller_state,
        step_core=base.step,
        read_radar=_V17.read_smb1_radar,
        radar_reason=_V17.v15._radar_reason,
        select_plan=_V17.v16.best_coherent_live_radar_plan,
        looks_grounded=_V17.v16._looks_grounded,
        emergency_jump_plan=_V17.v16._emergency_jump_plan,
        schedule_buttons=v11._schedule_buttons,
        schedule_label=_V17.v14._schedule_label,
        save_checkpoint=base.save_checkpoint,
        publish_json=v11._atomic_json,
        append_timeline=_V17._append_timeline,
        persist_terminal=_V17._persist_terminal,
        viewer_factory=_V17.NesWebViewer,
        format_radar_strip=_V17.format_radar_strip,
        log=v11._log,
        bootstrap_schedule=v11.BOOTSTRAP_SCHEDULE,
        jump_names=_V17.v15._JUMP_NAMES,
        radar_lookahead_px=int(_V17.v15.RADAR_LOOKAHEAD_PX),
        enemy_trigger_px=int(_V17.v15.RADAR_ENEMY_TRIGGER_PX),
        gap_trigger_px=int(_V17.v15.RADAR_GAP_TRIGGER_PX),
        obstacle_trigger_px=int(_V17.v15.RADAR_OBSTACLE_TRIGGER_PX),
        risk_cutoff=lambda: float(_V17.v14._RISK_CUTOFF),
        ui_stride=int(_V17.UI_STRIDE),
    )


def _v28_checkpoint_request_enricher() -> AuthorityContinuationRequestEnricher:
    """Bind V28 continuation memory to the stable checkpoint-request seam."""

    return AuthorityContinuationRequestEnricher(
        _V28._AUTHORITY_PLAN_MEMORY,
        proof_horizon=int(_V28.COLLECT_PROOF_HORIZON),
        projector=_V28.schedule_window,
    )


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
    target_selector=collect_target_from_radar,
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
        NamedRunReset("v25-live-objective", _reset_v25_live_objective),
    )
)
_AUTHORITY_RUN_SETUP_PLAN = AuthorityRunSetupPlan(
    steps=(
        NamedRunSetup("v30-collect-proof-horizon", _setup_v30_collect_runtime),
    )
)
_V23_BOOTSTRAP_SETUP_PLAN = AuthorityRunSetupPlan(
    steps=(
        NamedRunSetup("v23-process-job", _setup_v23_process_job),
        NamedRunSetup("v23-surrogate-model", _setup_v23_surrogate_model),
        NamedRunSetup("v23-runtime-dir", _setup_v23_runtime_dir),
        NamedRunSetup("v23-live-stack", _setup_v23_live_stack),
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
    """Run current-root Star speculation without mutating authority action history."""

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
    """Run current authority with stable reset/setup/instrumentation/loop ownership."""

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

    v11._log(
        "Planner V35: synchronous current-root Star micro-MPC enabled | "
        "pause live frames during 8x4f exact reward search; V26 SURVIVE remains higher authority"
    )
    try:
        with _AUTHORITY_RUNTIME_SCOPE.controller_layer(capture_layer):
            _AUTHORITY_RUN_RESET_PLAN.reset_through("v32-collect-response-cache")
            v11._log(
                "Planner V34: eager COLLECT handoffs enabled | "
                f"handoffs={v34.COLLECT_HANDOFF_FRAMES}; progressive worker publish; "
                "per-branch first-seen deadlines; lineage + proof lease remain authoritative"
            )
            v11._log(
                "Planner V33: authority-observed COLLECT deadline enabled | "
                f"deadline={_V33._DEADLINE_FRAMES}f; late workers remain telemetry-only and cannot reopen cohorts"
            )
            v11._log(
                "Planner V32: deadline-closed COLLECT cohorts enabled | "
                f"full quorum before {_V32._cohort_deadline_frames()}f; close partial cohort at latest useful handoff"
            )
            _AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)
            _AUTHORITY_RUN_RESET_PLAN.reset_named("v29-collect-response-cache")
            v11._log(
                "Planner V29: coherent delayed COLLECT enabled | "
                "cache by generation/root/worker; wait for worker quorum before lineage + reward ranking"
            )
            _AUTHORITY_RUN_RESET_PLAN.reset_named("v28-authority-plan-memory")
            enricher = _v28_checkpoint_request_enricher()
            v11._log(
                "Planner V28: delay-compensated COLLECT enabled | "
                f"handoffs={_V28.COLLECT_HANDOFF_FRAMES} proof-horizon={_V28.COLLECT_PROOF_HORIZON}f; "
                "branch-level reward proofs + current-plan continuation anchor + lineage lease"
            )
            with installed_checkpoint_request_enricher(enricher):
                _AUTHORITY_RUN_RESET_PLAN.reset_named("v27-progress-response-cache")
                _AUTHORITY_RUN_RESET_PLAN.reset_named("v27-authority-action-ledger")
                v11._log(
                    "Planner V27: bounded multi-chunk PROGRESS search enabled | "
                    f"depth={_V27.SEARCH_DEPTH} top-k={_V27.SEARCH_TOP_K} surrogate rank/prune -> exact Mesen; "
                    "delayed proofs require action-lineage match + remaining proof lease; "
                    "V26 SURVIVE + V25 COLLECT remain higher authority"
                )
                with _AUTHORITY_RUNTIME_SCOPE.controller_layer(
                    authority_action_recording_layer(_V27._AUTHORITY_ACTION_LEDGER)
                ):
                    _AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")
                    v11._log(
                        "Planner V26: current scene SURVIVE guards enabled | "
                        f"gap<={v26.v15.RADAR_GAP_TRIGGER_PX}px grounded/airborne single-root rearm+15f hold; "
                        "landing corridor enemy preemption restored"
                    )
                    _AUTHORITY_RUN_RESET_PLAN.reset_named("v25-live-objective")
                    v11._log(
                        "Planner V25: sticky COLLECT objective enabled | "
                        f"reward-prefix={v25._REWARD_PREFIX_FRAMES}f vocab={len(v25.REWARD_BEAM_CHUNKS_WITH_HOLD)} "
                        "exact Mesen prefix safety + native collection proof"
                    )
                    v11._log(
                        "Planner V24: latency-tolerant forward model enabled | "
                        f"prefix={v23.EXECUTION_PREFIX_FRAMES}f partial-safe selection + tail continuation"
                    )
                    bootstrap = _V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)
                    runtime_dir = bootstrap["v23-runtime-dir"]
                    v11._log(f"Runtime IPC : {runtime_dir}")
                    v11._log(
                        "Planner V23: live Mesen forward model enabled | "
                        f"horizon={v23.LIVE_TRAJECTORY_HORIZON}f prefix={v23.EXECUTION_PREFIX_FRAMES}f "
                        "HORIZON=UNKNOWN; safe resolved branches only"
                    )
                    v11._log(
                        "Forward model: Mesen outcomes are final branch authority; "
                        "async source age is recorded and only the short action prefix is rebased live"
                    )
                    live_control = _build_live_authority_control()
                    return live_control.run(args)
    finally:
        _LIVE_AUTHORITY_CORE = None


def _install_v35_overrides() -> None:
    v34._install_v34_overrides()

    v26.install_lower_plan_delegate(_best_collect_or_progress)

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v35_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
