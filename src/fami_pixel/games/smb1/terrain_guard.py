"""Fail-closed live guard for near-field SMB1 terrain gaps.

This module does not try to turn the rolling block buffers into a complete world
model.  It handles one narrower contract exposed by field evidence: when the
*current authoritative* radar already reports a near gap, an asynchronous stale
progress plan must not be allowed to release A and replace an active jump merely
because that old branch reached a short landing event.

Mesen remains transition authority.  This guard only decides whether a current
near-gap observation requires a bounded jump extension while Mario is already
airborne.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import Smb1Action, action_to_nes_buttons


DEFAULT_NEAR_GAP_PX = 80
DEFAULT_AIRBORNE_EXTEND_FRAMES = 4


@dataclass(frozen=True)
class TerrainGapGuard:
    gap_dx: int
    grounded: bool
    mode: str


def near_gap_guard(
    radar: dict,
    *,
    trigger_px: int = DEFAULT_NEAR_GAP_PX,
) -> TerrainGapGuard | None:
    """Classify a currently observed near gap for live preemption.

    ``None`` means there is no current positive near-gap observation.  The
    function intentionally does not infer SAFE from ``nearest_gap_dx is None``;
    absence of a decoded gap can still be UNKNOWN when terrain coverage is
    limited.
    """

    if trigger_px < 0:
        raise ValueError("trigger_px must be >= 0")
    raw_gap = radar.get("nearest_gap_dx")
    if raw_gap is None:
        return None
    try:
        gap_dx = int(raw_gap)
    except (TypeError, ValueError):
        return None
    if gap_dx < 0 or gap_dx > int(trigger_px):
        return None

    grounded = bool(radar.get("grounded", False))
    return TerrainGapGuard(
        gap_dx=gap_dx,
        grounded=grounded,
        mode="grounded-rearm" if grounded else "airborne-extend",
    )


def airborne_gap_extension_schedule(
    *,
    prefix_frames: int = DEFAULT_AIRBORNE_EXTEND_FRAMES,
) -> list[dict[str, int]]:
    """Hold RIGHT+A+B for one bounded prefix, then release A but keep running.

    V11-style authority holds the final schedule segment while waiting for the
    next plan.  Appending RIGHT+B therefore prevents an unresolved planner cycle
    from turning the bounded extension into an unbounded A hold.
    """

    if prefix_frames <= 0:
        raise ValueError("prefix_frames must be > 0")
    return [
        {
            "buttons": int(action_to_nes_buttons(Smb1Action.RIGHT_A_B)),
            "frames": int(prefix_frames),
        },
        {
            "buttons": int(action_to_nes_buttons(Smb1Action.RIGHT_B)),
            "frames": 1,
        },
    ]
