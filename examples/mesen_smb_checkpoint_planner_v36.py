#!/usr/bin/env python3
"""V36 planner: in-process native exact-boundary Star micro-MPC.

V35 proved the synchronous current-root Star policy but evaluated every 4-frame
candidate by repeatedly restoring and stepping the live authority emulator. The
Mesen fork now exposes one separate threadless speculative emulator that can
restore the same serializable root and execute variable-input schedules at the
exact same debugger PPU-period boundaries as the authoritative path.

V36 keeps V35 policy semantics unchanged:

* V26 remains the higher SURVIVE / landing authority.
* Star COLLECT uses the same eight V25 4-frame chunks and ranking.
* Death, level-complete, and native collection proof are evaluated after every
  exact frame witness; extra native execution after an early-stop witness is
  ignored semantically.
* Native runner failure falls back to V35's existing live-core exact path.

Mesen save-state capture has one important boundary rule: a PPU-cycle debugger
pause can be mid CPU instruction, while a resumable save-state root must be at a
safe CPU instruction boundary. Mesen's AcquireLock()/DebugBreakHelper settles
such a paused root to the next instruction boundary before serializing it. V36
therefore treats the *post-capture* state as the canonical decision root,
verifies that the speculative clone matches that root byte-for-byte, and re-reads
all SMB semantics from the canonical root before evaluating candidates.

Only the transition substrate changes. SMB1 decoding, reward proof, and ranking
remain in fami-pixel rather than moving into Mesen.
"""

from __future__ import annotations

import time

from fami_pixel.adapters.mesen import (
    MesenLoadError,
    NativeSpecRunner,
    get_nes_controller_state,
    read_nes_internal_ram,
)
from fami_pixel.games.smb1 import (
    GameEventType,
    decode_smb1_state,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)
from fami_pixel.games.smb1.radar import (
    ADDR_PLAYER_STATUS,
    ADDR_STAR_INVINCIBLE_TIMER,
    decode_smb1_radar,
    read_smb1_radar,
)
from fami_pixel.games.smb1.reward_beam import (
    buttons_for_chunk_frame,
    reward_collection_proven,
)
from fami_pixel.games.smb1.reward_motion import reward_intercept_key_motion
from fami_pixel.games.smb1.reward_target import decode_active_reward_target

import mesen_smb_checkpoint_planner_v35 as v35


PLANNER_NAME = "v36-native-exact-current-root-star-collect"

_NATIVE_SPEC_RUNNER: NativeSpecRunner | None = None
_NATIVE_SPEC_CORE = None


def _release_native_spec_runner() -> None:
    global _NATIVE_SPEC_RUNNER, _NATIVE_SPEC_CORE
    runner = _NATIVE_SPEC_RUNNER
    _NATIVE_SPEC_RUNNER = None
    _NATIVE_SPEC_CORE = None
    if runner is not None:
        try:
            runner.release()
        except Exception:
            pass


def _first_difference(before: bytes, after: bytes) -> int | None:
    return next(
        (
            index
            for index, (left, right) in enumerate(zip(before, after))
            if left != right
        ),
        None,
    )


def _native_runner_for_current_root(
    core,
    *,
    current_frame: int,
) -> tuple[NativeSpecRunner, int, int | None]:
    """Establish and verify one canonical serializable decision root.

    ``FamiPixelSpecInitFromLive`` / ``CaptureRootFromLive`` serialize under
    Mesen's safe save-state lock. If the authority is paused in the middle of a
    CPU instruction, that lock may finish the instruction before serialization.
    This is root *establishment*, not speculative rollout. The post-capture
    state is authoritative for the decision and must still be on the same native
    frame. Once established, live and speculative frame/RAM/controller witnesses
    must be identical.
    """

    global _NATIVE_SPEC_RUNNER, _NATIVE_SPEC_CORE

    requested_frame = int(current_frame)
    before_frame = int(core.frame_count())
    before_ram = read_nes_internal_ram(core)
    if before_frame != requested_frame:
        raise MesenLoadError(
            "native speculative pre-capture frame mismatch: "
            f"requested={requested_frame} live={before_frame}"
        )

    if _NATIVE_SPEC_RUNNER is None or _NATIVE_SPEC_CORE is not core:
        _release_native_spec_runner()
        runner = NativeSpecRunner(core)
        runner.initialize_from_live()
        _NATIVE_SPEC_RUNNER = runner
        _NATIVE_SPEC_CORE = core
    else:
        runner = _NATIVE_SPEC_RUNNER
        runner.capture_root_from_live()

    canonical_frame = int(core.frame_count())
    if canonical_frame != requested_frame:
        raise MesenLoadError(
            "native root settlement crossed a frame boundary: "
            f"requested={requested_frame} canonical={canonical_frame}"
        )

    live_ram = read_nes_internal_ram(core)
    spec_ram = runner.ram()
    if live_ram != spec_ram:
        first = _first_difference(live_ram, spec_ram)
        detail = "none" if first is None else f"0x{first:04X}"
        raise MesenLoadError(
            "native speculative root RAM differs from canonical live root: "
            f"first_difference={detail}"
        )

    spec_frame = int(runner.frame_count())
    if spec_frame != canonical_frame:
        raise MesenLoadError(
            "native speculative root frame differs from canonical live root: "
            f"live={canonical_frame} spec={spec_frame}"
        )

    live_controller = int(get_nes_controller_state(core, 0))
    spec_controller = int(runner.controller(0))
    if live_controller != spec_controller:
        raise MesenLoadError(
            "native speculative root controller differs from canonical live root: "
            f"live=0x{live_controller:02X} spec=0x{spec_controller:02X}"
        )

    return runner, canonical_frame, _first_difference(before_ram, live_ram)


def _capability_radar_from_ram(ram: bytes) -> dict[str, int]:
    """Return only native capability fields needed for collection proof.

    V25 decodes the full scene radar after every debugger-stepped frame because
    the live API exposes state incrementally. V36 already owns a coherent 2 KiB
    RAM witness at every exact boundary. Collection proof depends only on native
    player status / Star timer (1-Up remains intentionally unproven), so decoding
    terrain, enemy slots, and block buffers on all 32 speculative witnesses adds
    Python overhead without changing policy semantics. Full radar is decoded once
    at the semantic stop witness for final enemy-clearance/ranking evidence.
    """

    return {
        "player_status": int(ram[ADDR_PLAYER_STATUS]),
        "star_invincible_timer": int(ram[ADDR_STAR_INVINCIBLE_TIMER]),
    }


def _evaluate_native_reward_chunk(
    runner: NativeSpecRunner,
    chunk,
    *,
    root_observation,
    root_radar: dict,
    target_type: str,
    request_radar: dict,
):
    """Reproduce V25's per-frame reward-prefix semantics from native witnesses."""

    runner.reset_to_root()
    schedule = tuple(
        buttons_for_chunk_frame(chunk, offset)
        for offset in range(int(chunk.frame_count))
    )
    witnesses = runner.run_schedule(schedule, port=0)
    if len(witnesses) != int(chunk.frame_count):
        raise RuntimeError(
            f"native schedule returned {len(witnesses)} witnesses for {chunk.frame_count} frames"
        )

    baseline_status = int(
        request_radar.get("player_status", root_radar.get("player_status", 0))
    )
    baseline_timer = int(
        request_radar.get(
            "star_invincible_timer",
            root_radar.get("star_invincible_timer", 0),
        )
    )

    previous = root_observation
    current = previous
    died = False
    won = False
    collected = False
    frames = 0
    stop_ram = None
    stop_controller = None

    for witness in witnesses:
        frames += 1
        state = decode_smb1_state(witness.ram)
        current = observation_from_state(int(witness.frame_count), state)
        events = derive_game_events(previous, current)
        died = any(event.kind == GameEventType.DIED for event in events)
        won = any(event.kind == GameEventType.LEVEL_COMPLETED for event in events)
        collected = reward_collection_proven(
            target_type,
            baseline_player_status=baseline_status,
            baseline_star_timer=baseline_timer,
            radar=_capability_radar_from_ram(witness.ram),
        )
        stop_ram = witness.ram
        stop_controller = int(witness.controller)
        if died or won or collected:
            break
        previous = current

    if stop_ram is None:
        raise RuntimeError("native reward schedule produced no usable witness")

    radar = decode_smb1_radar(
        stop_ram,
        player_x=int(current.mario_x_abs),
    ).to_payload()
    tracked = decode_active_reward_target(
        stop_ram,
        player_x=int(current.mario_x_abs),
    )
    if tracked is not None and str(tracked.get("type")) != str(target_type):
        tracked = None

    enemy = radar.get("nearest_enemy_dx")
    enemy_dx = None if enemy is None else int(enemy)
    key = reward_intercept_key_motion(
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
        "frames": int(frames),
        "observation": current,
        "radar": radar,
        "target": tracked,
        "reward_key": key,
        "nearest_enemy_dx": enemy_dx,
        "controller": stop_controller,
    }


def _sync_native_star_plan_untracked(
    core,
    *,
    current_frame: int,
    live_radar: dict,
) -> dict | None:
    """Evaluate V35's Star vocabulary on the separate native speculative core."""

    if v35._COLLECT_PROGRESS_CONTROL.target_type(live_radar) != "star":
        return None

    started = time.perf_counter()
    runner, root_frame, settlement_first_difference = _native_runner_for_current_root(
        core,
        current_frame=int(current_frame),
    )

    root_state = read_smb1_state(core)
    root_observation = observation_from_state(root_frame, root_state)
    root_x = int(root_observation.mario_x_abs)
    root_radar = read_smb1_radar(core, player_x=root_x).to_payload()
    if v35._COLLECT_PROGRESS_CONTROL.target_type(root_radar) != "star":
        return None

    best_chunk = None
    best_outcome = None
    best_key = None
    candidate_rows: list[dict] = []

    for chunk in v35.v25.REWARD_BEAM_CHUNKS_WITH_HOLD:
        branch_started = time.perf_counter()
        outcome = _evaluate_native_reward_chunk(
            runner,
            chunk,
            root_observation=root_observation,
            root_radar=root_radar,
            target_type="star",
            request_radar=root_radar,
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
                "controller": outcome["controller"],
                "compute_ms": round((time.perf_counter() - branch_started) * 1000.0, 3),
            }
        )
        if safe and (best_key is None or key > best_key):
            best_key = key
            best_chunk = chunk
            best_outcome = outcome

    total_ms = (time.perf_counter() - started) * 1000.0
    root_settled = settlement_first_difference is not None
    settlement_offset = (
        None
        if settlement_first_difference is None
        else int(settlement_first_difference)
    )

    if best_chunk is None or best_outcome is None:
        v35.v23._latest_forward_meta = {
            "forward_model_status": "native-sync-star-no-safe-prefix",
            "objective_mode": "COLLECT",
            "collect_target_type": "star",
            "forward_model_source_frame": root_frame,
            "forward_model_source_age_frames": 0,
            "sync_collect_engine": "native-exact-boundary",
            "sync_collect_root_settled": root_settled,
            "sync_collect_root_settlement_first_difference": settlement_offset,
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
        "worker": "authority-native-spec-star",
        "root_frame": root_frame,
        "trajectory_root_frame": root_frame,
        "trajectory_source_age_frames": 0,
        "age": 0,
        "planner_mode": "collect-native-exact-current-root",
        "target_reward_type": "star",
        "candidate": candidate,
        "schedule": v35.v25.reward_chunk_schedule(best_chunk),
        "score": [1 if collected else 0, *reward_key],
        "progress": int(current.mario_x_abs) - root_x,
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
            f"collect-native-exact-current-root[star,{candidate},root:{root_frame},"
            f"proof:{int(best_outcome['frames'])}f]"
        ),
        "live_radar": dict(root_radar),
        "sync_collect_engine": "native-exact-boundary",
        "sync_collect_root_settled": root_settled,
        "sync_collect_root_settlement_first_difference": settlement_offset,
        "sync_collect_candidates": candidate_rows,
    }

    v35.v23._latest_forward_meta = {
        "forward_model_status": "selected-native-exact-current-root-star",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
        "forward_model_generation": -1,
        "forward_model_plan": candidate,
        "forward_model_event": result["trajectory_event"],
        "forward_model_source_frame": root_frame,
        "forward_model_source_age_frames": 0,
        "forward_model_simulated_frames": int(best_outcome["frames"]),
        "forward_model_reward_collected": collected,
        "reward_target_dx": result["reward_target_dx"],
        "sync_collect_engine": "native-exact-boundary",
        "sync_collect_root_settled": root_settled,
        "sync_collect_root_settlement_first_difference": settlement_offset,
        "sync_collect_candidates_evaluated": len(candidate_rows),
        "sync_collect_compute_ms": round(total_ms, 3),
        "sync_collect_candidates": candidate_rows,
    }
    return result


def _sync_native_star_plan(core, *, current_frame: int, live_radar: dict) -> dict | None:
    """Run native Star speculation without mutating authority action history."""

    ledger = v35._COLLECT_PROGRESS_CONTROL.eager_collect.ledger
    with ledger.suspend_recording():
        return _sync_native_star_plan_untracked(
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
    """Prefer native exact Star MPC; safely fall back to V35 on native failure."""

    target_type = v35._COLLECT_PROGRESS_CONTROL.target_type(live_radar)
    core = v35._LIVE_AUTHORITY_CORE
    if target_type == "star" and core is not None:
        try:
            result = _sync_native_star_plan(
                core,
                current_frame=int(current_frame),
                live_radar=live_radar,
            )
            if result is not None:
                return result
        except (MesenLoadError, RuntimeError, ValueError) as exc:
            v35.v11._log(
                "Planner V36: native Star speculation unavailable; "
                f"falling back to V35 live-core exact path ({exc})"
            )
            _release_native_spec_runner()
            v35.v23._latest_forward_meta = {
                "forward_model_status": "native-sync-star-fallback",
                "objective_mode": "COLLECT",
                "collect_target_type": "star",
                "forward_model_source_frame": int(current_frame),
                "forward_model_source_age_frames": 0,
                "native_spec_error": str(exc),
            }

    return v35._best_collect_or_progress(
        response_paths,
        current_frame=current_frame,
        freshness=freshness,
        last_applied_generation=last_applied_generation,
        live_radar=live_radar,
    )


def authority_main(args) -> int:
    """Reuse V35 authority composition while owning native spec-runner lifetime."""

    _release_native_spec_runner()
    v35.v11._log(
        "Planner V36: native exact-boundary current-root Star MPC enabled | "
        "persistent in-process speculative Mesen; serializable-root settlement; "
        "V35 live-core exact fallback retained"
    )
    try:
        return v35.authority_main(args)
    finally:
        _release_native_spec_runner()


def _install_v36_overrides() -> None:
    v35._install_v35_overrides()
    v35.v26.install_lower_plan_delegate(_best_collect_or_progress)
    v35.v23.PLANNER_NAME = PLANNER_NAME
    v35.v23.authority_main = authority_main
    v35.v23.__file__ = __file__


def main() -> int:
    _install_v36_overrides()
    return v35.v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
