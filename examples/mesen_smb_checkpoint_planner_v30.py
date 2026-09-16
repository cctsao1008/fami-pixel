#!/usr/bin/env python3
"""V30 planner: shared-prefix delay-compensated COLLECT tree.

V28 made delayed reward branches lineage-correct and V29 made their worker cohort
coherent.  Their remaining cost defect is mechanical: every reward branch replays
the same deterministic authority continuation from the root before reaching its
8f/12f handoff.

V30 evaluates that continuation once on worker 0, saves exact Mesen branch-point
states at each handoff, publishes a small manifest, and lets every reward worker
restore those shared states.  Branch payloads still carry the complete root-based
schedule, so V29's authority-side action-lineage and proof-lease validation is
unchanged.

The proof horizon is derived from the configured source-age target:

    proof_horizon >= plan_freshness + next_commit

With the default 16f planning-latency window and 4f commitment this is 20f.  For
8 reward chunks, handoffs at 8f/12f, and one continuation anchor, the exact-step
budget drops from 340 root-replayed frames to 180 shared-tree frames.  This is a
compute-budget/source-level gate only; no World 1-1 validation claim is made.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.games.smb1.action_lineage import schedule_buttons_at
from fami_pixel.games.smb1.collect_delay import (
    collect_proof_rank_key,
    compose_delayed_collect_schedule,
    continuation_anchor_schedule,
)
from fami_pixel.games.smb1.collect_shared_tree import (
    CollectTreeBudget,
    minimum_proof_horizon,
)

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v28 as v28
import mesen_smb_checkpoint_planner_v29 as v29


PLANNER_NAME = "v30-shared-prefix-delay-collect"
SHARED_TRUNK_OWNER = 0
SHARED_TRUNK_WAIT_S = 2.0


def _proof_horizon(args) -> int:
    """Cover the configured async age target plus the next exact commitment."""

    commit = int(v23.EXECUTION_PREFIX_FRAMES)
    source_age_target = max(0, int(args.plan_freshness))
    return max(
        max(int(value) for value in v28.COLLECT_HANDOFF_FRAMES) + commit,
        minimum_proof_horizon(
            max_source_age=source_age_target,
            commit_frames=commit,
        ),
    )


def _tree_paths(req: dict, generation: int, root_frame: int) -> tuple[Path, dict[int, Path]]:
    root = Path(req["checkpoint"])
    stem = f"collect-tree-g{int(generation):06d}-r{int(root_frame):08d}"
    manifest = root.parent / f"{stem}.json"
    states = {
        int(handoff): root.parent / f"{stem}-h{int(handoff):02d}.mss"
        for handoff in v28.COLLECT_HANDOFF_FRAMES
    }
    return manifest, states


def _collection_baseline(request_radar: dict, radar: dict) -> tuple[int, int]:
    return (
        int(request_radar.get("player_status", radar.get("player_status", 0))),
        int(request_radar.get("star_invincible_timer", radar.get("star_invincible_timer", 0))),
    )


def _collection_now(
    target_type: str,
    *,
    baseline_status: int,
    baseline_timer: int,
    radar: dict,
) -> bool:
    return bool(
        v25.reward_collection_proven(
            target_type,
            baseline_player_status=baseline_status,
            baseline_star_timer=baseline_timer,
            radar=radar,
        )
    )


def _build_shared_trunk(
    core,
    args,
    req: dict,
    *,
    generation: int,
    root_frame: int,
    root_x: int,
    root_engine: int,
    target_type: str,
    proof_horizon: int,
) -> dict | None:
    """Evaluate the common continuation once and publish exact handoff states."""

    continuation = req.get("authority_continuation_schedule") or ()
    if not continuation:
        return None

    manifest_path, state_paths = _tree_paths(req, generation, root_frame)
    checkpoint = Path(req["checkpoint"])
    request_radar = dict(req.get("radar") or {})
    base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)

    previous = v25.observation_from_state(core.frame_count(), v25.read_smb1_state(core))
    current = previous
    radar = v25.read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
    baseline_status, baseline_timer = _collection_baseline(request_radar, radar)
    collected = False
    collection_frame = None
    branchpoints: dict[str, dict] = {}
    exact_steps = 0

    max_handoff = max(int(value) for value in v28.COLLECT_HANDOFF_FRAMES)
    for offset in range(max_handoff):
        buttons = schedule_buttons_at(continuation, offset)
        if buttons is None:
            return None
        v25.set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(1, max(1, int(float(args.step_timeout) * 1000.0)))
        exact_steps += 1
        current = v25.observation_from_state(core.frame_count(), v25.read_smb1_state(core))
        radar = v25.read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
        events = v25.derive_game_events(previous, current)
        died = any(event.kind == v25.GameEventType.DIED for event in events)
        won = any(event.kind == v25.GameEventType.LEVEL_COMPLETED for event in events)
        now_collected = _collection_now(
            target_type,
            baseline_status=baseline_status,
            baseline_timer=baseline_timer,
            radar=radar,
        )
        if now_collected and not collected:
            collected = True
            collection_frame = exact_steps
        if died or won:
            # Terminal continuation is rare and deserves the already-correct V28
            # root-replay fallback rather than a partial shared tree.
            return None

        if exact_steps in state_paths:
            path = state_paths[exact_steps]
            frame, x, engine = base.save_checkpoint(core, path)
            branchpoints[str(exact_steps)] = {
                "checkpoint": str(path),
                "frame": int(frame),
                "x": int(x),
                "engine": int(engine),
                "collected": bool(collected),
                "collection_frame": collection_frame,
            }
        previous = current

    payload = {
        "generation": int(generation),
        "root_frame": int(root_frame),
        "target_reward_type": str(target_type),
        "proof_horizon": int(proof_horizon),
        "handoffs": [int(value) for value in v28.COLLECT_HANDOFF_FRAMES],
        "trunk_exact_steps": int(exact_steps),
        "branchpoints": branchpoints,
    }
    if len(branchpoints) != len(tuple(v28.COLLECT_HANDOFF_FRAMES)):
        return None
    if not v11._atomic_json(manifest_path, payload):
        return None
    return payload


def _valid_manifest(
    payload: dict | None,
    *,
    generation: int,
    root_frame: int,
    target_type: str,
    proof_horizon: int,
) -> bool:
    if not payload:
        return False
    try:
        if int(payload.get("generation", -1)) != int(generation):
            return False
        if int(payload.get("root_frame", -1)) != int(root_frame):
            return False
        if str(payload.get("target_reward_type")) != str(target_type):
            return False
        if int(payload.get("proof_horizon", -1)) != int(proof_horizon):
            return False
    except (TypeError, ValueError):
        return False
    points = payload.get("branchpoints")
    if not isinstance(points, dict):
        return False
    for handoff in v28.COLLECT_HANDOFF_FRAMES:
        point = points.get(str(int(handoff)))
        if not isinstance(point, dict):
            return False
        path = Path(str(point.get("checkpoint") or ""))
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    return True


def _shared_trunk(
    core,
    args,
    req: dict,
    *,
    generation: int,
    root_frame: int,
    root_x: int,
    root_engine: int,
    target_type: str,
    proof_horizon: int,
) -> tuple[dict | None, bool]:
    """Return shared manifest and whether this worker paid the trunk cost."""

    manifest_path, _state_paths = _tree_paths(req, generation, root_frame)
    if int(args.worker_index) == SHARED_TRUNK_OWNER:
        payload = _build_shared_trunk(
            core,
            args,
            req,
            generation=generation,
            root_frame=root_frame,
            root_x=root_x,
            root_engine=root_engine,
            target_type=target_type,
            proof_horizon=proof_horizon,
        )
        return payload, payload is not None

    deadline = time.monotonic() + SHARED_TRUNK_WAIT_S
    while time.monotonic() < deadline:
        payload = v11._read_json(manifest_path)
        if _valid_manifest(
            payload,
            generation=generation,
            root_frame=root_frame,
            target_type=target_type,
            proof_horizon=proof_horizon,
        ):
            return dict(payload), False
        time.sleep(0.002)
    return None, False


def _restore_branchpoint(core, point: dict) -> None:
    base.restore_checkpoint(
        core,
        Path(point["checkpoint"]),
        int(point["frame"]),
        int(point["x"]),
        int(point["engine"]),
    )


def _finish_collect_suffix(
    core,
    full_schedule: list[dict],
    *,
    start_age: int,
    proof_horizon: int,
    target_type: str,
    request_radar: dict,
    trunk_point: dict,
    step_timeout: float,
) -> dict:
    """Evaluate only the post-handoff suffix while reporting root-relative proof age."""

    previous = v25.observation_from_state(core.frame_count(), v25.read_smb1_state(core))
    current = previous
    radar = v25.read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
    baseline_status, baseline_timer = _collection_baseline(request_radar, radar)
    collected = bool(trunk_point.get("collected", False))
    collection_frame = trunk_point.get("collection_frame")
    died = False
    won = False
    frames = int(start_age)
    suffix_steps = 0

    for offset in range(int(start_age), int(proof_horizon)):
        buttons = schedule_buttons_at(full_schedule, offset)
        if buttons is None:
            raise RuntimeError("shared collect schedule is empty/malformed")
        v25.set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(1, max(1, int(float(step_timeout) * 1000.0)))
        suffix_steps += 1
        frames += 1
        current = v25.observation_from_state(core.frame_count(), v25.read_smb1_state(core))
        radar = v25.read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
        events = v25.derive_game_events(previous, current)
        died = any(event.kind == v25.GameEventType.DIED for event in events)
        won = any(event.kind == v25.GameEventType.LEVEL_COMPLETED for event in events)
        now_collected = _collection_now(
            target_type,
            baseline_status=baseline_status,
            baseline_timer=baseline_timer,
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
        "suffix_steps": int(suffix_steps),
        "observation": current,
        "target": tracked,
        "reward_key": tuple(reward_key),
    }


def _unique_reward_chunks_for_worker(worker_index: int, worker_count: int):
    """Shard the fixed reward vocabulary without duplicating chunks on extra workers."""

    return tuple(
        chunk
        for index, chunk in enumerate(v25.REWARD_BEAM_CHUNKS_WITH_HOLD)
        if index % max(1, int(worker_count)) == int(worker_index)
    )


def _coverage_response(
    *,
    generation: int,
    worker: int,
    root_frame: int,
    target_type: str,
    proof_horizon: int,
    compute_ms: float,
) -> dict:
    """Let an idle worker satisfy cohort quorum without duplicating exact branches."""

    return {
        "generation": int(generation),
        "worker": int(worker),
        "root_frame": int(root_frame),
        "planner_mode": "collect",
        "target_reward_type": str(target_type),
        "candidate": f"collect_worker_{int(worker)}_coverage",
        "schedule": [{"buttons": 0, "frames": 1}],
        "reward_prefix_safe": False,
        "reward_collected": False,
        "reward_key": [],
        "trajectory_event": "coverage_only",
        "trajectory_frames": 0,
        "collect_proof_horizon": int(proof_horizon),
        "compute_ms": round(float(compute_ms), 3),
        "branch_proofs": [],
        "search_mesen_evaluated": 0,
        "collect_shared_tree": True,
        "collect_exact_frame_steps": 0,
        "collect_naive_equivalent_steps": 0,
    }


def _search_collect_payload_shared(
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
    """Evaluate delayed COLLECT by restoring one shared continuation tree."""

    continuation = req.get("authority_continuation_schedule") or ()
    if not continuation:
        return v28._search_collect_payload(
            core,
            args,
            req,
            generation=generation,
            root_frame=root_frame,
            root_x=root_x,
            root_engine=root_engine,
            target_type=target_type,
        )

    started = time.perf_counter()
    proof_horizon = _proof_horizon(args)
    manifest, owns_trunk = _shared_trunk(
        core,
        args,
        req,
        generation=generation,
        root_frame=root_frame,
        root_x=root_x,
        root_engine=root_engine,
        target_type=target_type,
        proof_horizon=proof_horizon,
    )
    if not _valid_manifest(
        manifest,
        generation=generation,
        root_frame=root_frame,
        target_type=target_type,
        proof_horizon=proof_horizon,
    ):
        # Cross-worker state sharing is an optimization, never a correctness
        # dependency. Fall back to V28's independently replayed exact branches.
        return v28._search_collect_payload(
            core,
            args,
            req,
            generation=generation,
            root_frame=root_frame,
            root_x=root_x,
            root_engine=root_engine,
            target_type=target_type,
        )

    request_radar = dict(req.get("radar") or {})
    chunks = _unique_reward_chunks_for_worker(args.worker_index, args.worker_count)
    if not chunks and int(args.worker_index) != SHARED_TRUNK_OWNER:
        return _coverage_response(
            generation=generation,
            worker=int(args.worker_index),
            root_frame=root_frame,
            target_type=target_type,
            proof_horizon=proof_horizon,
            compute_ms=(time.perf_counter() - started) * 1000.0,
        )

    branch_proofs: list[dict] = []
    suffix_steps_total = 0
    branchpoints = dict(manifest["branchpoints"])

    for chunk in chunks:
        reward_schedule = v25.reward_chunk_schedule(chunk)
        for handoff in v28.COLLECT_HANDOFF_FRAMES:
            point = dict(branchpoints[str(int(handoff))])
            full_schedule = compose_delayed_collect_schedule(
                continuation,
                reward_schedule,
                handoff_frames=int(handoff),
            )
            _restore_branchpoint(core, point)
            branch_started = time.perf_counter()
            outcome = _finish_collect_suffix(
                core,
                full_schedule,
                start_age=int(handoff),
                proof_horizon=proof_horizon,
                target_type=target_type,
                request_radar=request_radar,
                trunk_point=point,
                step_timeout=args.step_timeout,
            )
            suffix_steps_total += int(outcome["suffix_steps"])
            proof = v28._collect_proof_payload(
                generation=generation,
                worker=int(args.worker_index),
                root_frame=root_frame,
                target_type=target_type,
                candidate=f"collect_delay{int(handoff)}_{chunk.name}",
                schedule=full_schedule,
                outcome=outcome,
                compute_ms=(time.perf_counter() - branch_started) * 1000.0,
                handoff_frames=int(handoff),
                continuation_anchor=False,
            )
            proof["collect_proof_horizon"] = int(proof_horizon)
            proof["collect_shared_prefix_frames"] = int(handoff)
            proof["collect_suffix_exact_steps"] = int(outcome["suffix_steps"])
            branch_proofs.append(proof)

    if int(args.worker_index) == SHARED_TRUNK_OWNER:
        latest_handoff = max(int(value) for value in v28.COLLECT_HANDOFF_FRAMES)
        point = dict(branchpoints[str(latest_handoff)])
        anchor_schedule = continuation_anchor_schedule(
            continuation,
            proof_horizon=proof_horizon,
        )
        _restore_branchpoint(core, point)
        branch_started = time.perf_counter()
        outcome = _finish_collect_suffix(
            core,
            anchor_schedule,
            start_age=latest_handoff,
            proof_horizon=proof_horizon,
            target_type=target_type,
            request_radar=request_radar,
            trunk_point=point,
            step_timeout=args.step_timeout,
        )
        suffix_steps_total += int(outcome["suffix_steps"])
        proof = v28._collect_proof_payload(
            generation=generation,
            worker=int(args.worker_index),
            root_frame=root_frame,
            target_type=target_type,
            candidate=v28.CONTINUATION_CANDIDATE,
            schedule=anchor_schedule,
            outcome=outcome,
            compute_ms=(time.perf_counter() - branch_started) * 1000.0,
            handoff_frames=proof_horizon,
            continuation_anchor=True,
        )
        proof["collect_proof_horizon"] = int(proof_horizon)
        proof["collect_shared_prefix_frames"] = int(latest_handoff)
        proof["collect_suffix_exact_steps"] = int(outcome["suffix_steps"])
        branch_proofs.append(proof)

    if not branch_proofs:
        return _coverage_response(
            generation=generation,
            worker=int(args.worker_index),
            root_frame=root_frame,
            target_type=target_type,
            proof_horizon=proof_horizon,
            compute_ms=(time.perf_counter() - started) * 1000.0,
        )

    safe = [proof for proof in branch_proofs if bool(proof.get("reward_prefix_safe", False))]
    best = max(safe or branch_proofs, key=collect_proof_rank_key)
    payload = dict(best)
    trunk_steps = int(manifest.get("trunk_exact_steps", 0)) if owns_trunk else 0
    exact_steps = trunk_steps + suffix_steps_total
    payload["compute_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    payload["search_mesen_evaluated"] = len(branch_proofs)
    payload["branch_proofs"] = branch_proofs
    payload["authority_continuation_available"] = True
    payload["authority_continuation_candidate"] = req.get("authority_continuation_candidate")
    payload["collect_shared_tree"] = True
    payload["collect_shared_trunk_owner"] = bool(owns_trunk)
    payload["collect_shared_trunk_frames"] = int(trunk_steps)
    payload["collect_suffix_exact_steps"] = int(suffix_steps_total)
    payload["collect_exact_frame_steps"] = int(exact_steps)
    payload["collect_naive_equivalent_steps"] = len(branch_proofs) * int(proof_horizon)
    payload["collect_proof_horizon"] = int(proof_horizon)
    return payload


def authority_main(args) -> int:
    proof_horizon = _proof_horizon(args)
    # V28 request projection and V29 retention both read this module global in the
    # authority process. Worker processes derive the same horizon from their args.
    v28.COLLECT_PROOF_HORIZON = int(proof_horizon)
    budget = CollectTreeBudget(
        chunk_count=len(v25.REWARD_BEAM_CHUNKS_WITH_HOLD),
        handoffs=tuple(int(value) for value in v28.COLLECT_HANDOFF_FRAMES),
        proof_horizon=int(proof_horizon),
    )
    v11._log(
        "Planner V30: shared-prefix delayed COLLECT enabled | "
        f"proof-horizon={proof_horizon}f handoffs={tuple(v28.COLLECT_HANDOFF_FRAMES)} "
        f"budget naive={budget.naive_exact_steps}f shared={budget.shared_exact_steps}f "
        f"saved={budget.saved_exact_steps}f"
    )
    return v29.authority_main(args)


def _install_v30_overrides() -> None:
    v29._install_v29_overrides()

    # V28's worker dispatch resolves this global at runtime. Replace only its
    # COLLECT evaluator; V29 cohort selection and V27 PROGRESS remain untouched.
    v28._search_collect_payload = _search_collect_payload_shared

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v30_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
