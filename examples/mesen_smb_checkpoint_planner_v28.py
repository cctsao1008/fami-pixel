#!/usr/bin/env python3
"""V28 planner: delay-compensated COLLECT on the V27 lineage contract.

V27 made delayed PROGRESS proofs exact again: historical Mesen results are usable
only while live authority has executed the same action lineage and enough proof
lease remains. V25 COLLECT still violated that contract because a worker proved
only a four-frame reward prefix, then the selector rebased a delayed response to
``root=current`` and replayed it from phase zero.

V28 keeps V26 scene safety and V27 bounded PROGRESS unchanged, but moves COLLECT
to an asynchronous MPC-style handoff contract:

    current authority plan at checkpoint root
      -> exact continuation prefix (8f / 12f)
      -> one V5 reward chunk
      -> A-released continuation tail
      -> exact Mesen proof to a 24f bounded horizon

Workers return every branch proof rather than only their shard-local winner. A
continuation-only anchor is also evaluated so at least one candidate describes
"keep doing the currently applied plan" while reward branches compute. Authority
filters delayed reward results by actual NES action lineage and remaining proof
lease before reward ranking. The original proof root/phase is preserved.

This is a source-level integration gate. It does not make a live-validation claim.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.games.smb1.action_lineage import schedule_buttons_at
from fami_pixel.games.smb1.collect_delay import (
    DEFAULT_COLLECT_HANDOFF_FRAMES,
    DEFAULT_COLLECT_PROOF_HORIZON,
    collect_proof_rank_key,
    compose_delayed_collect_schedule,
    continuation_anchor_schedule,
    schedule_window,
    select_lineage_collect_proof,
)

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v24 as v24
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26
import mesen_smb_checkpoint_planner_v27 as v27


PLANNER_NAME = "v28-delay-compensated-collect"
COLLECT_HANDOFF_FRAMES = DEFAULT_COLLECT_HANDOFF_FRAMES
COLLECT_PROOF_HORIZON = DEFAULT_COLLECT_PROOF_HORIZON
CONTINUATION_CANDIDATE = "collect_continue_authority"

_BASE_V26_PLAN = v26._best_v26_plan
_LATEST_AUTHORITY_PLAN: dict | None = None


def _remember_authority_plan(plan: dict | None) -> None:
    """Remember the actual selected schedule so the next checkpoint can warm-start."""

    global _LATEST_AUTHORITY_PLAN
    if not plan:
        return
    schedule = plan.get("schedule") or ()
    if not schedule:
        return
    try:
        root_frame = int(plan["root_frame"])
    except (KeyError, TypeError, ValueError):
        return
    _LATEST_AUTHORITY_PLAN = {
        "root_frame": root_frame,
        "candidate": str(plan.get("candidate") or "unknown"),
        "schedule": [dict(segment) for segment in schedule],
    }


def _continuation_schedule_for_frame(frame: int) -> list[dict[str, int]]:
    """Project the currently selected live schedule from ``frame`` for 24f."""

    if _LATEST_AUTHORITY_PLAN is None:
        return []
    root = int(_LATEST_AUTHORITY_PLAN["root_frame"])
    age = int(frame) - root
    if age < 0:
        return []
    return schedule_window(
        _LATEST_AUTHORITY_PLAN["schedule"],
        start_age=age,
        frames=COLLECT_PROOF_HORIZON,
    )


def _best_v28_plan(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Preserve V26 safety ordering and remember the schedule it actually selects."""

    result = _BASE_V26_PLAN(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )
    _remember_authority_plan(result)
    return result


def _collect_target_from_request(req: dict) -> str | None:
    return v25._collect_target_from_radar(dict(req.get("radar") or {}))


def _evaluate_collect_schedule(
    core,
    schedule: list[dict],
    *,
    target_type: str,
    request_radar: dict,
    step_timeout: float,
) -> dict:
    """Evaluate one exact delayed reward schedule for the bounded proof horizon."""

    previous = v25.observation_from_state(core.frame_count(), v25.read_smb1_state(core))
    current = previous
    radar = v25.read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
    baseline_status = int(request_radar.get("player_status", radar.get("player_status", 0)))
    baseline_timer = int(request_radar.get("star_invincible_timer", radar.get("star_invincible_timer", 0)))
    died = False
    won = False
    collected = False
    collection_frame = None
    frames = 0

    for offset in range(COLLECT_PROOF_HORIZON):
        buttons = schedule_buttons_at(schedule, offset)
        if buttons is None:
            raise RuntimeError("collect proof schedule is empty/malformed")
        v25.set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(1, max(1, int(float(step_timeout) * 1000.0)))
        frames += 1
        current = v25.observation_from_state(core.frame_count(), v25.read_smb1_state(core))
        radar = v25.read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
        events = v25.derive_game_events(previous, current)
        died = any(event.kind == v25.GameEventType.DIED for event in events)
        won = any(event.kind == v25.GameEventType.LEVEL_COMPLETED for event in events)
        now_collected = v25.reward_collection_proven(
            target_type,
            baseline_player_status=baseline_status,
            baseline_star_timer=baseline_timer,
            radar=radar,
        )
        if now_collected and not collected:
            collected = True
            collection_frame = frames
        if died or won:
            break
        previous = current

    try:
        v25.set_nes_controller_state(core, 0, 0x00)
    except Exception:
        pass

    try:
        tracked = v25.read_active_reward_target(core, player_x=current.mario_x_abs)
    except Exception:
        tracked = None
    if tracked is not None and str(tracked.get("type")) != target_type:
        tracked = None

    enemy = radar.get("nearest_enemy_dx")
    enemy_dx = None if enemy is None else int(enemy)
    reward_key = v25.reward_intercept_key_motion(
        reward=tracked,
        nearest_enemy_dx=enemy_dx,
        mario_y=int(current.mario_y),
        player_x_speed=int(current.player_x_speed),
        player_y_speed=int(current.player_y_speed),
    )
    return {
        "died": bool(died),
        "won": bool(won),
        "collected": bool(collected),
        "collection_frame": collection_frame,
        "frames": int(frames),
        "observation": current,
        "target": tracked,
        "reward_key": tuple(reward_key),
    }


def _collect_proof_payload(
    *,
    generation: int,
    worker: int,
    root_frame: int,
    target_type: str,
    candidate: str,
    schedule: list[dict],
    outcome: dict,
    compute_ms: float,
    handoff_frames: int,
    continuation_anchor: bool = False,
) -> dict:
    current = outcome["observation"]
    target = outcome["target"]
    died = bool(outcome["died"])
    collected = bool(outcome["collected"])
    return {
        "generation": int(generation),
        "worker": int(worker),
        "root_frame": int(root_frame),
        "planner_mode": "collect",
        "target_reward_type": str(target_type),
        "candidate": str(candidate),
        "schedule": [dict(segment) for segment in schedule],
        "compute_ms": round(float(compute_ms), 3),
        "terminal": "death" if died else "none",
        "reward_prefix_safe": not died,
        "reward_collected": collected,
        "reward_key": list(outcome["reward_key"]),
        "reward_target_dx": None if target is None else int(target.get("dx", 0)),
        "reward_target_state": None if target is None else int(target.get("state", 0)),
        "reward_target_y": None if target is None else int(target.get("y", 0)),
        "reward_prefix_frames": int(outcome["frames"]),
        "trajectory_event": "reward_collected" if collected else ("win" if outcome["won"] else "prefix_alive"),
        "trajectory_frames": int(outcome["frames"]),
        "trajectory_end_x": int(current.mario_x_abs),
        "trajectory_end_y": int(current.mario_y),
        "trajectory_reward_collected": collected,
        "trajectory_target_reward_type": str(target_type),
        "collect_handoff_frames": int(handoff_frames),
        "collect_continuation_anchor": bool(continuation_anchor),
        "collect_collection_frame": outcome.get("collection_frame"),
        "collect_proof_horizon": int(COLLECT_PROOF_HORIZON),
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
    }


def _collect_candidate_schedules(req: dict, args) -> list[tuple[str, list[dict], int, bool]]:
    """Build delayed reward branches plus one current-plan continuation anchor."""

    continuation = req.get("authority_continuation_schedule") or ()
    chunks = v25._reward_chunks_for_worker(args.worker_index, args.worker_count)
    candidates: list[tuple[str, list[dict], int, bool]] = []

    if continuation:
        for chunk in chunks:
            reward_schedule = v25.reward_chunk_schedule(chunk)
            for handoff in COLLECT_HANDOFF_FRAMES:
                schedule = compose_delayed_collect_schedule(
                    continuation,
                    reward_schedule,
                    handoff_frames=int(handoff),
                )
                if schedule:
                    candidates.append(
                        (
                            f"collect_delay{int(handoff)}_{chunk.name}",
                            schedule,
                            int(handoff),
                            False,
                        )
                    )

        # Only one worker spends exact-Mesen budget on the shared warm-start
        # anchor; duplicating it across every shard adds no information.
        if int(args.worker_index) == 0:
            anchor = continuation_anchor_schedule(
                continuation,
                proof_horizon=COLLECT_PROOF_HORIZON,
            )
            if anchor:
                candidates.append(
                    (
                        CONTINUATION_CANDIDATE,
                        anchor,
                        COLLECT_PROOF_HORIZON,
                        True,
                    )
                )
    else:
        # Fail operationally soft but semantically exact: before a continuation
        # warm start exists, evaluate immediate reward branches. Lineage filtering
        # will accept them only if authority actually executed the same prefix.
        for chunk in chunks:
            candidates.append(
                (
                    f"collect_now_{chunk.name}",
                    v25.reward_chunk_schedule(chunk),
                    0,
                    False,
                )
            )

    return candidates


def _search_collect_payload(
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
    """Evaluate every delayed COLLECT branch in this worker shard."""

    checkpoint = Path(req["checkpoint"])
    request_radar = dict(req.get("radar") or {})
    branch_proofs: list[dict] = []
    started = time.perf_counter()

    for candidate, schedule, handoff, is_anchor in _collect_candidate_schedules(req, args):
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        branch_started = time.perf_counter()
        outcome = _evaluate_collect_schedule(
            core,
            schedule,
            target_type=target_type,
            request_radar=request_radar,
            step_timeout=args.step_timeout,
        )
        branch_proofs.append(
            _collect_proof_payload(
                generation=generation,
                worker=int(args.worker_index),
                root_frame=root_frame,
                target_type=target_type,
                candidate=candidate,
                schedule=schedule,
                outcome=outcome,
                compute_ms=(time.perf_counter() - branch_started) * 1000.0,
                handoff_frames=handoff,
                continuation_anchor=is_anchor,
            )
        )

    if not branch_proofs:
        raise RuntimeError("V28 COLLECT worker has no candidate schedules")

    safe = [proof for proof in branch_proofs if bool(proof.get("reward_prefix_safe", False))]
    best = max(safe or branch_proofs, key=collect_proof_rank_key)
    payload = dict(best)
    payload["compute_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    payload["search_mesen_evaluated"] = len(branch_proofs)
    payload["branch_proofs"] = branch_proofs
    payload["authority_continuation_available"] = bool(req.get("authority_continuation_schedule"))
    payload["authority_continuation_candidate"] = req.get("authority_continuation_candidate")
    return payload


def shadow_worker_main(args) -> int:
    """Use V27 PROGRESS workers and V28 delay-compensated COLLECT workers."""

    assert args.request is not None and args.response is not None
    worker_home = Path(f"{args.shadow_home}-v28-{args.worker_index}")
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
        target_type = _collect_target_from_request(req)
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
            else:
                payload = _search_collect_payload(
                    core,
                    args,
                    req,
                    generation=generation,
                    root_frame=root_frame,
                    root_x=root_x,
                    root_engine=root_engine,
                    target_type=target_type,
                )
            v11._atomic_json(args.response, payload)
        except Exception as exc:
            v11._atomic_json(
                args.response,
                {
                    "generation": generation,
                    "worker": args.worker_index,
                    "root_frame": root_frame,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def _best_collect_or_progress(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Apply exact delayed-COLLECT validity; delegate non-COLLECT to V27."""

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

    selection = select_lineage_collect_proof(
        responses,
        ledger=v27._AUTHORITY_ACTION_LEDGER,
        current_frame=current_frame,
        last_applied_generation=last_applied_generation,
        target_type=target_type,
        commit_frames=v23.EXECUTION_PREFIX_FRAMES,
        retention_frames=max(int(freshness), COLLECT_PROOF_HORIZON),
    )
    if selection.proof is None:
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-lineage-collect-proof",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_responses": len(responses),
            "collect_lineage_valid_count": int(selection.valid_count),
            "collect_lineage_rejected": dict(selection.rejected),
            "collect_inspected_groups": int(selection.inspected_groups),
        }
        return None

    result = dict(selection.proof)
    source_root_frame = int(result["root_frame"])
    source_age = int(current_frame) - source_root_frame
    result["trajectory_root_frame"] = source_root_frame
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = source_root_frame
    result["age"] = source_age
    result["guard_mode"] = (
        f"collect-lineage[{target_type},{result.get('candidate')},"
        f"src-age:{source_age}f,lease:{result.get('proof_remaining_frames')}f]"
    )
    result["live_radar"] = dict(live_radar or {})

    v23._latest_forward_meta = {
        "forward_model_status": "selected-lineage-collect-proof",
        "objective_mode": "COLLECT",
        "collect_target_type": target_type,
        "collect_target_sticky": bool(live_radar.get("collect_target_sticky", False)),
        "forward_model_generation": result.get("generation"),
        "forward_model_plan": result.get("candidate"),
        "forward_model_event": result.get("trajectory_event"),
        "forward_model_source_frame": source_root_frame,
        "forward_model_source_age_frames": source_age,
        "forward_model_simulated_frames": result.get("trajectory_frames"),
        "forward_model_end_x": result.get("trajectory_end_x"),
        "forward_model_end_y": result.get("trajectory_end_y"),
        "forward_model_reward_collected": result.get("reward_collected"),
        "forward_model_target_reward_type": target_type,
        "reward_target_dx": result.get("reward_target_dx"),
        "reward_target_state": result.get("reward_target_state"),
        "reward_target_y": result.get("reward_target_y"),
        "collect_handoff_frames": result.get("collect_handoff_frames"),
        "collect_continuation_anchor": result.get("collect_continuation_anchor"),
        "collect_lineage_matched_frames": result.get("lineage_matched_frames"),
        "collect_proof_remaining_frames": result.get("proof_remaining_frames"),
        "collect_lineage_valid_count": int(selection.valid_count),
        "collect_lineage_rejected": dict(selection.rejected),
        "collect_inspected_groups": int(selection.inspected_groups),
    }
    return result


def _v28_schedule_label(candidate_name: str) -> str:
    if candidate_name == CONTINUATION_CANDIDATE:
        return "COLLECT CONTINUATION ANCHOR | exact current-plan warm start"
    if candidate_name.startswith("collect_delay"):
        return f"COLLECT DELAY-COMPENSATED {candidate_name[len('collect_') :]}"
    if candidate_name.startswith("collect_now_"):
        return f"COLLECT IMMEDIATE {candidate_name[len('collect_now_') :]}"
    return v27._v27_schedule_label(candidate_name)


def authority_main(args) -> int:
    """Inject current-plan continuation into checkpoint requests; V27 records lineage."""

    global _LATEST_AUTHORITY_PLAN
    _LATEST_AUTHORITY_PLAN = None
    original_atomic_json = v11._atomic_json

    def continuation_atomic_json(path, payload):
        data = dict(payload)
        if (
            "checkpoint" in data
            and "frame" in data
            and "generation" in data
            and "worker" not in data
        ):
            continuation = _continuation_schedule_for_frame(int(data["frame"]))
            if continuation:
                data["authority_continuation_schedule"] = continuation
                data["authority_continuation_candidate"] = (
                    None
                    if _LATEST_AUTHORITY_PLAN is None
                    else _LATEST_AUTHORITY_PLAN.get("candidate")
                )
                data["authority_continuation_root_frame"] = (
                    None
                    if _LATEST_AUTHORITY_PLAN is None
                    else _LATEST_AUTHORITY_PLAN.get("root_frame")
                )
        return original_atomic_json(path, data)

    v11._atomic_json = continuation_atomic_json
    v11._log(
        "Planner V28: delay-compensated COLLECT enabled | "
        f"handoffs={COLLECT_HANDOFF_FRAMES} proof-horizon={COLLECT_PROOF_HORIZON}f; "
        "branch-level reward proofs + current-plan continuation anchor + lineage lease"
    )
    try:
        return v27.authority_main(args)
    finally:
        v11._atomic_json = original_atomic_json


def _install_v28_overrides() -> None:
    # Start from the complete V27 PROGRESS/lineage stack.
    v27._install_v27_overrides()

    # V26 owns SURVIVE and landing preemption. Replace only its lower-priority
    # V25 delegate so COLLECT uses the exact delayed-proof contract and ordinary
    # PROGRESS remains V27's lineage-aware bounded search.
    v26._BASE_V25_PLAN = _best_collect_or_progress
    v23._best_forward_plan = _best_v28_plan

    # Child processes launched through v23.__file__ must execute this worker,
    # otherwise they would fall back to V25's four-frame shard-local COLLECT.
    v23.shadow_worker_main = shadow_worker_main
    v23.PLANNER_NAME = PLANNER_NAME
    v23._forward_schedule_label = _v28_schedule_label
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v28_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
