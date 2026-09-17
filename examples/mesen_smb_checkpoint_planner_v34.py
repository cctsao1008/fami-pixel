#!/usr/bin/env python3
"""V34 planner: eager +4f COLLECT with per-handoff asynchronous deadlines.

V27/V25 is the known-good Star-interception behavioral baseline: once a reward is
visible it can redirect the next four-frame control quantum.  V28 preserved proof
correctness but delayed every normal reward maneuver to +8f/+12f behind the
currently selected authority trajectory.  For a moving Star that delay is a
functional regression.

V34 keeps the post-V27 correctness work and restores an early branch point:

    current authority continuation
        -> +4f reward handoff
        -> +8f reward handoff
        -> +12f reward handoff

Workers publish cumulative branch bundles after each handoff stage rather than
waiting for the whole 4/8/12 search to finish.  Authority stamps every branch
when it first observes it.  A +4f proof first seen at age 5 is permanently late;
it cannot inherit an earlier worker arrival timestamp or reopen the expired
handoff.  Within one root, the earliest ready handoff is considered first.  Each
stage requires full active-worker coverage before its handoff, but closes with
whatever arrived when that handoff is reached.  Exact action lineage and proof
lease still filter branches before reward ranking, and selected plans retain the
historical root/real phase.

World 1-1 remains blocked until a deterministic Star checkpoint proves collection.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.games.smb1.collect_delay import (
    collect_proof_rank_key,
    compose_delayed_collect_schedule,
    continuation_anchor_schedule,
    select_lineage_collect_proof,
)
from fami_pixel.games.smb1.collect_handoff_deadline import (
    HandoffDeadlineCollectResponseCache,
)

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26
import mesen_smb_checkpoint_planner_v27 as v27
import mesen_smb_checkpoint_planner_v28 as v28

# Set the behavioral contract before importing the later wrappers.  V30/V32/V33
# read v28.COLLECT_HANDOFF_FRAMES dynamically (and V33 derives max=12 at import).
COLLECT_HANDOFF_FRAMES = (4, 8, 12)
v28.COLLECT_HANDOFF_FRAMES = COLLECT_HANDOFF_FRAMES

import mesen_smb_checkpoint_planner_v29 as v29
import mesen_smb_checkpoint_planner_v30 as v30
import mesen_smb_checkpoint_planner_v31 as v31
import mesen_smb_checkpoint_planner_v32 as v32
import mesen_smb_checkpoint_planner_v33 as v33


PLANNER_NAME = "v34-eager-handoff-collect"
_HANDOFF_CACHE = HandoffDeadlineCollectResponseCache(
    deadline_frames=max(COLLECT_HANDOFF_FRAMES)
)


def _install_handoff_cache() -> HandoffDeadlineCollectResponseCache:
    v29._COLLECT_RESPONSE_CACHE = _HANDOFF_CACHE
    v33._DEADLINE_CACHE = _HANDOFF_CACHE
    v33._DEADLINE_FRAMES = max(COLLECT_HANDOFF_FRAMES)
    return _HANDOFF_CACHE


def _active_collect_workers(worker_count: int) -> set[int]:
    return {
        worker
        for worker in range(max(1, int(worker_count)))
        if v30._unique_reward_chunks_for_worker(worker, worker_count)
    }


def _evaluate_stage_proofs(
    core,
    args,
    req: dict,
    *,
    generation: int,
    root_frame: int,
    root_x: int,
    root_engine: int,
    target_type: str,
    handoff_frames: int,
) -> list[dict]:
    """Evaluate one handoff stage only far enough to certify the next 4f commit."""

    continuation = req.get("authority_continuation_schedule") or ()
    if not continuation:
        return []
    chunks = v30._unique_reward_chunks_for_worker(args.worker_index, args.worker_count)
    if not chunks:
        return []

    checkpoint = Path(req["checkpoint"])
    request_radar = dict(req.get("radar") or {})
    horizon = int(handoff_frames) + int(v23.EXECUTION_PREFIX_FRAMES)
    proofs: list[dict] = []

    original_horizon = int(v28.COLLECT_PROOF_HORIZON)
    v28.COLLECT_PROOF_HORIZON = horizon
    try:
        for chunk in chunks:
            reward_schedule = v25.reward_chunk_schedule(chunk)
            schedule = compose_delayed_collect_schedule(
                continuation,
                reward_schedule,
                handoff_frames=int(handoff_frames),
            )
            if not schedule:
                continue
            base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
            branch_started = time.perf_counter()
            outcome = v28._evaluate_collect_schedule(
                core,
                schedule,
                target_type=target_type,
                request_radar=request_radar,
                step_timeout=args.step_timeout,
            )
            proof = v28._collect_proof_payload(
                generation=generation,
                worker=int(args.worker_index),
                root_frame=root_frame,
                target_type=target_type,
                candidate=f"collect_delay{int(handoff_frames)}_{chunk.name}",
                schedule=schedule,
                outcome=outcome,
                compute_ms=(time.perf_counter() - branch_started) * 1000.0,
                handoff_frames=int(handoff_frames),
                continuation_anchor=False,
            )
            proof["collect_proof_horizon"] = horizon
            proof["collect_progressive_stage"] = True
            proof["collect_stage_horizon_frames"] = horizon
            proofs.append(proof)
    finally:
        v28.COLLECT_PROOF_HORIZON = original_horizon
    return proofs


def _bundle_payload(
    proofs: list[dict],
    *,
    generation: int,
    worker: int,
    root_frame: int,
    target_type: str,
    started: float,
    published_handoff: int,
) -> dict:
    safe = [proof for proof in proofs if bool(proof.get("reward_prefix_safe", False))]
    if safe:
        best = max(safe, key=collect_proof_rank_key)
        payload = dict(best)
    elif proofs:
        payload = dict(proofs[0])
    else:
        payload = {
            "generation": int(generation),
            "worker": int(worker),
            "root_frame": int(root_frame),
            "planner_mode": "collect",
            "target_reward_type": str(target_type),
            "candidate": f"collect_worker_{int(worker)}_coverage",
            "schedule": [{"buttons": 0, "frames": 1}],
            "reward_prefix_safe": False,
            "trajectory_event": "coverage_only",
            "trajectory_frames": 0,
        }
    payload["branch_proofs"] = [dict(proof) for proof in proofs]
    payload["compute_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    payload["search_mesen_evaluated"] = len(proofs)
    payload["collect_progressive_publish"] = True
    payload["collect_published_through_handoff_frames"] = int(published_handoff)
    return payload


def _progressive_collect_worker(
    core,
    args,
    req: dict,
    *,
    generation: int,
    root_frame: int,
    root_x: int,
    root_engine: int,
    target_type: str,
) -> dict:
    """Publish +4, then +8, then +12 branch bundles for one generation."""

    continuation = req.get("authority_continuation_schedule") or ()
    if not continuation:
        # Do not revive V25's stale root=current rebasing.  Before a warm-start
        # exists there is no exact future handoff to certify, so remain coverage
        # only and let the currently applied authority schedule continue.
        return _bundle_payload(
            [],
            generation=generation,
            worker=int(args.worker_index),
            root_frame=root_frame,
            target_type=target_type,
            started=time.perf_counter(),
            published_handoff=0,
        )

    started = time.perf_counter()
    cumulative: list[dict] = []
    latest_payload: dict | None = None

    for handoff in COLLECT_HANDOFF_FRAMES:
        cumulative.extend(
            _evaluate_stage_proofs(
                core,
                args,
                req,
                generation=generation,
                root_frame=root_frame,
                root_x=root_x,
                root_engine=root_engine,
                target_type=target_type,
                handoff_frames=int(handoff),
            )
        )
        latest_payload = _bundle_payload(
            cumulative,
            generation=generation,
            worker=int(args.worker_index),
            root_frame=root_frame,
            target_type=target_type,
            started=started,
            published_handoff=int(handoff),
        )
        # Intentional multi-publish: authority may consume the +4 stage while
        # this worker is still calculating +8/+12.  Later writes are cumulative.
        v11._atomic_json(args.response, latest_payload)

    # Add one continuation anchor after reward stages. It is not allowed to
    # delay the +4 publication path.
    if int(args.worker_index) == 0:
        final_horizon = max(
            int(v30._proof_horizon(args)),
            max(COLLECT_HANDOFF_FRAMES) + int(v23.EXECUTION_PREFIX_FRAMES),
        )
        anchor_schedule = continuation_anchor_schedule(
            continuation,
            proof_horizon=final_horizon,
        )
        if anchor_schedule:
            checkpoint = Path(req["checkpoint"])
            base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
            original_horizon = int(v28.COLLECT_PROOF_HORIZON)
            v28.COLLECT_PROOF_HORIZON = final_horizon
            try:
                branch_started = time.perf_counter()
                outcome = v28._evaluate_collect_schedule(
                    core,
                    anchor_schedule,
                    target_type=target_type,
                    request_radar=dict(req.get("radar") or {}),
                    step_timeout=args.step_timeout,
                )
                anchor = v28._collect_proof_payload(
                    generation=generation,
                    worker=int(args.worker_index),
                    root_frame=root_frame,
                    target_type=target_type,
                    candidate=v28.CONTINUATION_CANDIDATE,
                    schedule=anchor_schedule,
                    outcome=outcome,
                    compute_ms=(time.perf_counter() - branch_started) * 1000.0,
                    handoff_frames=final_horizon,
                    continuation_anchor=True,
                )
                anchor["collect_proof_horizon"] = final_horizon
                cumulative.append(anchor)
            finally:
                v28.COLLECT_PROOF_HORIZON = original_horizon
            latest_payload = _bundle_payload(
                cumulative,
                generation=generation,
                worker=int(args.worker_index),
                root_frame=root_frame,
                target_type=target_type,
                started=started,
                published_handoff=max(COLLECT_HANDOFF_FRAMES),
            )
            v11._atomic_json(args.response, latest_payload)

    assert latest_payload is not None
    return latest_payload


def shadow_worker_main(args) -> int:
    """V27 PROGRESS plus progressively published V34 COLLECT stages."""

    assert args.request is not None and args.response is not None
    worker_home = Path(f"{args.shadow_home}-v34-{args.worker_index}")
    core = v25.MesenCore(args.dll)
    core.initialize_headless(worker_home)
    v25.configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        return 2
    core.initialize_debugger()

    last_generation = -1
    while True:
        req = v11._read_json(args.request)
        if req is None:
            time.sleep(0.001)
            continue
        generation = int(req.get("generation", -1))
        if generation <= last_generation:
            time.sleep(0.001)
            continue
        last_generation = generation

        root_frame = int(req["frame"])
        root_x = int(req["x"])
        root_engine = int(req["engine"])
        target_type = v25._collect_target_from_radar(dict(req.get("radar") or {}))
        try:
            if target_type is None:
                payload = v27._search_baseline_payload(
                    core,
                    args,
                    req,
                    generation=generation,
                    root_frame=root_frame,
                    root_x=root_x,
                    root_engine=root_engine,
                )
                v11._atomic_json(args.response, payload)
            else:
                payload = _progressive_collect_worker(
                    core,
                    args,
                    req,
                    generation=generation,
                    root_frame=root_frame,
                    root_x=root_x,
                    root_engine=root_engine,
                    target_type=target_type,
                )
                # The progressive worker already published every stage; repeat
                # only the final cumulative state for crash-safe visibility.
                v11._atomic_json(args.response, payload)
        except Exception as exc:
            v11._atomic_json(
                args.response,
                {
                    "generation": generation,
                    "worker": int(args.worker_index),
                    "root_frame": root_frame,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def _proofs_for_handoff(cohort: list[dict], handoff: int) -> tuple[list[dict], set[int]]:
    proofs: list[dict] = []
    workers: set[int] = set()
    for response in cohort:
        raw = response.get("branch_proofs")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            if bool(item.get("collect_continuation_anchor", False)):
                continue
            try:
                item_handoff = int(item.get("collect_handoff_frames", -1))
                worker = int(item.get("worker", response.get("worker", -1)))
            except (TypeError, ValueError):
                continue
            if item_handoff != int(handoff) or worker < 0:
                continue
            proofs.append(dict(item))
            workers.add(worker)
    return proofs, workers


def _anchor_proofs(cohort: list[dict]) -> list[dict]:
    anchors: list[dict] = []
    for response in cohort:
        raw = response.get("branch_proofs")
        if not isinstance(raw, list):
            continue
        anchors.extend(
            dict(item)
            for item in raw
            if isinstance(item, dict) and bool(item.get("collect_continuation_anchor", False))
        )
    return anchors


def _selected_result(selection, *, current_frame: int, live_radar: dict, handoff: int) -> dict:
    result = dict(selection.proof)
    source_root = int(result["root_frame"])
    source_age = int(current_frame) - source_root
    result["trajectory_root_frame"] = source_root
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = source_root
    result["age"] = source_age
    result["guard_mode"] = (
        f"collect-eager-handoff[{handoff}f,{result.get('candidate')},"
        f"src-age:{source_age}f,lease:{result.get('proof_remaining_frames')}f]"
    )
    result["live_radar"] = dict(live_radar or {})
    return result


def _best_collect_or_progress(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    target_type = v25._collect_target_from_radar(live_radar)
    if target_type is None:
        return v27._best_forward_plan_partial_v27(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
            live_radar,
        )

    responses: list[dict] = []
    for path in response_paths:
        response = v11._read_json(path)
        if response is not None:
            responses.append(dict(response))

    retention = max(int(freshness), int(v30._proof_horizon(type("A", (), {"plan_freshness": freshness})())))
    cache = _install_handoff_cache()
    cache.ingest(
        responses,
        current_frame=current_frame,
        last_applied_generation=last_applied_generation,
        retention_frames=retention,
        target_type=target_type,
    )

    expected_workers = len(response_paths)
    active_workers = _active_collect_workers(expected_workers)
    newest_wait = None
    newest_rejection = None

    for (generation, root_frame), cohort in cache.ordered_groups():
        age = int(current_frame) - int(root_frame)
        handoff_timing = cache.handoff_snapshot(
            generation=int(generation), root_frame=int(root_frame)
        )

        for handoff in COLLECT_HANDOFF_FRAMES:
            proofs, workers = _proofs_for_handoff(cohort, int(handoff))
            complete = active_workers.issubset(workers)
            closed = age >= int(handoff)

            if not proofs:
                if not closed:
                    newest_wait = (
                        generation,
                        root_frame,
                        handoff,
                        age,
                        workers,
                        handoff_timing,
                    )
                    break
                continue
            if not complete and not closed:
                newest_wait = (
                    generation,
                    root_frame,
                    handoff,
                    age,
                    workers,
                    handoff_timing,
                )
                break

            selection = select_lineage_collect_proof(
                proofs,
                ledger=v27._AUTHORITY_ACTION_LEDGER,
                current_frame=current_frame,
                last_applied_generation=last_applied_generation,
                target_type=target_type,
                commit_frames=v23.EXECUTION_PREFIX_FRAMES,
                retention_frames=retention,
            )
            if selection.proof is None:
                newest_rejection = (
                    generation,
                    root_frame,
                    handoff,
                    age,
                    workers,
                    selection,
                    handoff_timing,
                )
                continue

            result = _selected_result(
                selection,
                current_frame=current_frame,
                live_radar=live_radar,
                handoff=int(handoff),
            )
            v23._latest_forward_meta = {
                "forward_model_status": "selected-eager-handoff-collect-proof",
                "objective_mode": "COLLECT",
                "collect_target_type": target_type,
                "forward_model_generation": int(generation),
                "forward_model_plan": result.get("candidate"),
                "forward_model_event": result.get("trajectory_event"),
                "forward_model_source_frame": int(root_frame),
                "forward_model_source_age_frames": int(age),
                "forward_model_reward_collected": result.get("reward_collected"),
                "collect_handoff_frames": int(handoff),
                "collect_handoff_stage_complete": bool(complete),
                "collect_handoff_stage_closed": bool(closed),
                "collect_handoff_workers": sorted(workers),
                "collect_active_workers": sorted(active_workers),
                "collect_lineage_valid_count": int(selection.valid_count),
                "collect_lineage_rejected": dict(selection.rejected),
                "collect_proof_remaining_frames": result.get("proof_remaining_frames"),
                "collect_handoff_timing": handoff_timing,
            }
            return result
        else:
            # Reward handoffs are exhausted. A continuation anchor may keep the
            # existing plan exact, but it never outranks an available reward stage.
            anchors = _anchor_proofs(cohort)
            if anchors:
                selection = select_lineage_collect_proof(
                    anchors,
                    ledger=v27._AUTHORITY_ACTION_LEDGER,
                    current_frame=current_frame,
                    last_applied_generation=last_applied_generation,
                    target_type=target_type,
                    commit_frames=v23.EXECUTION_PREFIX_FRAMES,
                    retention_frames=retention,
                )
                if selection.proof is not None:
                    result = _selected_result(
                        selection,
                        current_frame=current_frame,
                        live_radar=live_radar,
                        handoff=int(selection.proof.get("collect_handoff_frames", 0)),
                    )
                    v23._latest_forward_meta = {
                        "forward_model_status": "selected-collect-continuation-anchor",
                        "objective_mode": "COLLECT",
                        "collect_target_type": target_type,
                        "forward_model_generation": int(generation),
                        "forward_model_plan": result.get("candidate"),
                        "forward_model_source_frame": int(root_frame),
                        "forward_model_source_age_frames": int(age),
                        "collect_handoff_timing": handoff_timing,
                    }
                    return result
            continue

        # A not-yet-closed earliest stage blocks later handoffs for this root.
        if newest_wait is not None:
            break

    if newest_wait is not None:
        generation, root_frame, handoff, age, workers, timing = newest_wait
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-eager-collect-handoff",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(age),
            "collect_waiting_handoff_frames": int(handoff),
            "collect_handoff_workers": sorted(workers),
            "collect_active_workers": sorted(active_workers),
            "collect_handoff_timing": timing,
        }
    elif newest_rejection is not None:
        generation, root_frame, handoff, age, workers, selection, timing = newest_rejection
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-lineage-valid-eager-collect-proof",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(age),
            "collect_handoff_frames": int(handoff),
            "collect_handoff_workers": sorted(workers),
            "collect_lineage_rejected": dict(selection.rejected),
            "collect_handoff_timing": timing,
        }
    else:
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-eager-collect-cohort",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
        }
    return None


def authority_main(args) -> int:
    _install_handoff_cache().clear()
    v11._log(
        "Planner V34: eager COLLECT handoffs enabled | "
        f"handoffs={COLLECT_HANDOFF_FRAMES}; progressive worker publish; "
        "per-branch first-seen deadlines; lineage + proof lease remain authoritative"
    )
    return v33.authority_main(args)


def _install_v34_overrides() -> None:
    v28.COLLECT_HANDOFF_FRAMES = COLLECT_HANDOFF_FRAMES
    v33._install_v33_overrides()
    _install_handoff_cache()

    v26._BASE_V25_PLAN = _best_collect_or_progress
    v23.shadow_worker_main = shadow_worker_main
    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v34_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
