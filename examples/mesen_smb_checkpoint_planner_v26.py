#!/usr/bin/env python3
"""V26 live planner: current-terrain gap preempts stale progress responses.

The V25 field run reproduced the long-standing World 1-1 pit failure.  The
important evidence was not merely the terminal death: the current authoritative
radar already reported the gap while Mario was airborne, but V24 partial-plan
responses then replaced the active jump with stale ``fm_run`` / ``fm_brake_jump``
responses whose old roots had only proven short landing events.

V26 fixes that policy inversion.  A *current* near-gap observation is scene
safety evidence and outranks asynchronous progress/reward selection.  If Mario
is already airborne, authority holds RIGHT+A+B for exactly one 4-frame control
quantum, appends RIGHT+B as the bounded continuation, then re-observes.  Grounded
near-gap behavior remains V16's existing re-arm emergency path.

This is not a World 1-1 coordinate script and does not claim that ``gap=None`` is
safe.  It only prevents an observed gap from being overwritten by a stale plan.
"""

from __future__ import annotations

from fami_pixel.games.smb1.terrain_guard import (
    airborne_gap_extension_schedule,
    near_gap_guard,
)

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25


PLANNER_NAME = "v26-current-gap-preemption"
GAP_EXTEND_CANDIDATE = "terrain_gap_extend4"
_BASE_V25_AUTHORITY = v25.authority_main
_BASE_V25_LABEL = v25._v25_schedule_label
_BASE_V25_PLAN = v25._best_v25_plan


def _gap_extend_plan(current_frame: int, live_radar: dict, *, gap_dx: int) -> dict:
    """Return one bounded airborne jump extension from current authority state."""

    return {
        "generation": -1,
        "worker": "current-terrain",
        "root_frame": int(current_frame),
        "candidate": GAP_EXTEND_CANDIDATE,
        "schedule": airborne_gap_extension_schedule(prefix_frames=4),
        "score": [1, 0, 0, 0],
        "progress": 0,
        "terminal": "none",
        "age": 0,
        "compute_ms": 0.0,
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
        "guard_mode": f"current-gap-airborne-extend[gap:{int(gap_dx)}]",
        "live_radar": dict(live_radar or {}),
        "terrain_gap_dx": int(gap_dx),
        "terrain_guard": "airborne-extend",
    }


def _best_v26_plan(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Make current positive gap evidence outrank stale async planner output."""

    gap = near_gap_guard(live_radar, trigger_px=v15.RADAR_GAP_TRIGGER_PX)
    if gap is not None and gap.mode == "airborne-extend":
        result = _gap_extend_plan(current_frame, live_radar, gap_dx=gap.gap_dx)
        v23._latest_forward_meta = {
            "forward_model_status": "current-gap-preempt",
            "objective_mode": live_radar.get("objective_mode", "PROGRESS"),
            "forward_model_plan": GAP_EXTEND_CANDIDATE,
            "forward_model_event": "current-gap",
            "terrain_gap_dx": gap.gap_dx,
            "terrain_guard": gap.mode,
        }
        return result

    # Grounded near-gap cases intentionally delegate.  V17/V16's authority loop
    # will replace a non-jump result with its established re-arm emergency jump.
    return _BASE_V25_PLAN(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )


def _v26_schedule_label(candidate_name: str) -> str:
    if candidate_name == GAP_EXTEND_CANDIDATE:
        return "TERRAIN GAP EXTEND: RIGHT+A+B 4f -> RIGHT+B tail"
    return _BASE_V25_LABEL(candidate_name)


def authority_main(args) -> int:
    v11._log(
        "Planner V26: current near-gap preemption enabled | "
        f"gap<={v15.RADAR_GAP_TRIGGER_PX}px airborne=RIGHT+A+B 4f then replan"
    )
    return _BASE_V25_AUTHORITY(args)


def _install_v26_overrides() -> None:
    v23.PLANNER_NAME = PLANNER_NAME
    v23._best_forward_plan = _best_v26_plan
    v23._forward_schedule_label = _v26_schedule_label
    v23.authority_main = authority_main
    v23.__file__ = __file__
    v15._JUMP_NAMES.add(GAP_EXTEND_CANDIDATE)


def main() -> int:
    # Preserve all V24/V25 process, reward, landing, watchdog, and evidence
    # layers; replace only the final live selector ordering around current gaps.
    v25.v24._install_v24_overrides()
    v25._install_v25_overrides()
    _install_v26_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
