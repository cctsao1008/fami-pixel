#!/usr/bin/env python3
"""V26 live planner: current-terrain gap escape outranks stale progress.

The V25 field run reproduced the long-standing World 1-1 pit failure. Current
authoritative radar already reported a near gap while Mario was airborne, but
V24 partial-plan responses then replaced the active jump with stale ``fm_run`` /
``fm_brake_jump`` responses rooted 12 frames earlier.

The first V26 draft tried a 4-frame RIGHT+A+B extension. The deterministic
``pit-v25-gen302`` Mesen replay falsified that policy: ``gap_extend4`` died, while
re-armed short/long jumps from the exact same frame/X/Y state landed safely.

V26 therefore uses a stateful SURVIVE commitment instead of repeatedly making a
one-quantum decision. Once a current near-gap observation catches Mario airborne,
authority starts the exact re-arm long-jump schedule:

    RIGHT+B 1f -> RIGHT+A+B 15f -> RIGHT+B tail

and keeps the *same root frame* on subsequent 4-frame control quanta. This means
the schedule advances rather than restarting, and lower-priority asynchronous
progress/reward responses cannot overwrite the crossing.

The generation-295 deterministic root then exposed a second integration bug:
Mesen reported a real jump->movement landing at Y=128 after 15 frames, but the
legacy live grounded heuristic required Y>=160. That would keep SURVIVE active
after an elevated-surface landing. V26 now installs one SMB1 support predicate
for both the V16 emergency path and the V20 landing/radar payload: Player_State=0,
normal Y page, and zero signed vertical speed. Elevated pipes/blocks therefore
count as supported landings without weakening the airborne guard.

This is scene-driven, not a World 1-1 coordinate script. ``gap=None`` is still
UNKNOWN rather than SAFE, so a temporary terrain-radar dropout cannot cancel an
already-started crossing commitment.
"""

from __future__ import annotations

from fami_pixel.games.smb1.terrain_guard import (
    gap_escape_schedule,
    near_gap_guard,
    player_support_grounded,
)

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v16 as v16
import mesen_smb_checkpoint_planner_v20 as v20
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25


PLANNER_NAME = "v26-current-gap-preemption"
GAP_ESCAPE_CANDIDATE = "terrain_gap_escape_rearm"
_BASE_V25_AUTHORITY = v25.authority_main
_BASE_V25_LABEL = v25._v25_schedule_label
_BASE_V25_PLAN = v25._best_v25_plan

_gap_commit_root_frame: int | None = None
_gap_commit_trigger_dx: int | None = None


def _looks_grounded_observation(observation) -> bool:
    """Use SMB1 movement/support state instead of a floor-Y shortcut."""

    return player_support_grounded(
        player_state=int(observation.player_state),
        player_y_high=int(observation.mario_y_high),
        player_y_speed=int(observation.player_y_speed),
    )


def _grounded_from_state(state) -> bool:
    """V20 radar equivalent of ``_looks_grounded_observation``."""

    return player_support_grounded(
        player_state=int(state.player_state),
        player_y_high=int(state.player_y_high),
        player_y_speed=int(state.player_y_speed),
    )


def _reset_gap_commitment() -> None:
    global _gap_commit_root_frame, _gap_commit_trigger_dx
    _gap_commit_root_frame = None
    _gap_commit_trigger_dx = None


def _start_gap_commitment(current_frame: int, gap_dx: int) -> None:
    global _gap_commit_root_frame, _gap_commit_trigger_dx
    _gap_commit_root_frame = int(current_frame)
    _gap_commit_trigger_dx = int(gap_dx)


def _gap_commitment_active(current_frame: int, live_radar: dict) -> bool:
    """Keep a started crossing until current authority proves Mario supported."""

    global _gap_commit_root_frame
    if _gap_commit_root_frame is None:
        return False

    # Do not clear on gap=None: #31 terrain coverage can be UNKNOWN. The positive
    # completion signal is current support/grounded evidence after at least one
    # frame of committed execution. In V26 that evidence includes elevated solid
    # surfaces because V20._grounded_from_state is replaced at install time.
    if (
        int(current_frame) > int(_gap_commit_root_frame)
        and bool(live_radar.get("grounded", False))
    ):
        _reset_gap_commitment()
        return False
    return True


def _gap_escape_plan(current_frame: int, live_radar: dict) -> dict:
    assert _gap_commit_root_frame is not None
    assert _gap_commit_trigger_dx is not None

    current_gap = live_radar.get("nearest_gap_dx")
    try:
        current_gap_dx = None if current_gap is None else int(current_gap)
    except (TypeError, ValueError):
        current_gap_dx = None

    age = max(0, int(current_frame) - int(_gap_commit_root_frame))
    return {
        "generation": -1,
        "worker": "current-terrain",
        "root_frame": int(_gap_commit_root_frame),
        "candidate": GAP_ESCAPE_CANDIDATE,
        "schedule": gap_escape_schedule(hold_frames=15),
        "score": [1, 0, 0, 0],
        "progress": 0,
        "terminal": "none",
        "age": age,
        "compute_ms": 0.0,
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
        "guard_mode": (
            "current-gap-rearm-commit["
            f"trigger:{int(_gap_commit_trigger_dx)},"
            f"current:{current_gap_dx},age:{age}f]"
        ),
        "live_radar": dict(live_radar or {}),
        "terrain_gap_dx": current_gap_dx,
        "terrain_gap_trigger_dx": int(_gap_commit_trigger_dx),
        "terrain_guard": "airborne-rearm-commit",
    }


def _best_v26_plan(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Prioritize a current/sticky gap crossing over stale async output."""

    if _gap_commitment_active(current_frame, live_radar):
        result = _gap_escape_plan(current_frame, live_radar)
        v23._latest_forward_meta = {
            "forward_model_status": "current-gap-commit",
            "objective_mode": "SURVIVE",
            "forward_model_plan": GAP_ESCAPE_CANDIDATE,
            "forward_model_event": "current-gap",
            "terrain_gap_dx": result.get("terrain_gap_dx"),
            "terrain_gap_trigger_dx": result.get("terrain_gap_trigger_dx"),
            "terrain_guard": result.get("terrain_guard"),
            "terrain_commit_age_frames": result.get("age"),
        }
        return result

    gap = near_gap_guard(live_radar, trigger_px=v15.RADAR_GAP_TRIGGER_PX)
    if gap is not None and gap.mode == "airborne-rearm-commit":
        _start_gap_commitment(current_frame, gap.gap_dx)
        return _best_v26_plan(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
            live_radar,
        )

    # Grounded near-gap cases intentionally delegate. V17/V16's current-radar
    # authority loop already replaces a non-jump result with its re-arm emergency
    # jump. V26 patches V16's grounded predicate so elevated support is included.
    # If that jump reaches a near-gap airborne state on the next quantum, this
    # layer takes over and keeps the crossing committed until the next support.
    return _BASE_V25_PLAN(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )


def _v26_schedule_label(candidate_name: str) -> str:
    if candidate_name == GAP_ESCAPE_CANDIDATE:
        return "TERRAIN GAP ESCAPE: RIGHT+B 1f -> RIGHT+A+B 15f -> RIGHT+B until supported"
    return _BASE_V25_LABEL(candidate_name)


def authority_main(args) -> int:
    _reset_gap_commitment()
    v11._log(
        "Planner V26: current near-gap SURVIVE commitment enabled | "
        f"gap<={v15.RADAR_GAP_TRIGGER_PX}px airborne=rearm+15f hold; keep root until support"
    )
    return _BASE_V25_AUTHORITY(args)


def _install_v26_overrides() -> None:
    # Historical planners keep their old behavior; only the V26 runtime receives
    # the stronger support predicate discovered by the gen295 exact-Mesen gate.
    v16._looks_grounded = _looks_grounded_observation
    v20._grounded_from_state = _grounded_from_state

    v23.PLANNER_NAME = PLANNER_NAME
    v23._best_forward_plan = _best_v26_plan
    v23._forward_schedule_label = _v26_schedule_label
    v23.authority_main = authority_main
    v23.__file__ = __file__
    v15._JUMP_NAMES.add(GAP_ESCAPE_CANDIDATE)


def main() -> int:
    # Preserve all V24/V25 process, reward, landing, watchdog, and evidence
    # layers; replace only the final live selector ordering around current gaps.
    v25.v24._install_v24_overrides()
    v25._install_v25_overrides()
    _install_v26_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
