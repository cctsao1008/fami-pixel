"""Fail-closed live guard for near-field SMB1 terrain gaps.

This module does not try to turn the rolling block buffers into a complete world
model. It handles one narrower contract exposed by field evidence: when the
*current authoritative* radar already reports a near gap, an asynchronous stale
progress plan must not be allowed to replace the jump state with a plan rooted in
older geometry.

The V25 generation-302 exact-Mesen regression falsified a simple 4-frame
RIGHT+A+B extension: that branch died, while re-armed short/long jumps from the
same state landed safely. The live guard therefore exposes a re-arm + sustained-A
escape schedule. The caller is responsible for keeping that schedule rooted at
the original commitment frame until authoritative landing evidence clears it.

A second deterministic root at generation 295 exposed a semantic mismatch in
the old live ``grounded`` heuristic: Mesen produced a real Player_State 1->0
landing at Y=128, while the live heuristic required Y>=160 and therefore would
have kept the crossing commitment active after an elevated-surface landing.
``player_support_grounded`` centralizes the stronger game-state contract used by
V26: Player_State=0, normal Y page, and zero vertical speed. It intentionally
does not require floor-level Y, so pipes/blocks can terminate a crossing too.

Mesen remains transition authority. ``nearest_gap_dx is None`` is never promoted
to SAFE here; once a crossing commitment starts, a temporary radar dropout is
insufficient reason to hand control back to stale asynchronous progress output.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import Smb1Action, action_to_nes_buttons


DEFAULT_NEAR_GAP_PX = 80
DEFAULT_GAP_REARM_HOLD_FRAMES = 15


@dataclass(frozen=True)
class TerrainGapGuard:
    gap_dx: int
    grounded: bool
    mode: str


def _signed_u8(value: int) -> int:
    raw = int(value) & 0xFF
    return raw - 0x100 if raw & 0x80 else raw


def player_support_grounded(
    *,
    player_state: int,
    player_y_high: int,
    player_y_speed: int,
) -> bool:
    """Return SMB1 support evidence without assuming ground-floor Y.

    ``Player_State`` is the same native state already used by the event layer:
    0 is normal left/right movement and 1 is jumping/falling. Requiring state 0,
    the normal playfield Y page, and zero signed vertical speed recognizes both
    ordinary ground and elevated solid support. The previous Y>=160 shortcut
    missed valid landings on higher terrain and could keep a SURVIVE commitment
    alive after Mesen had already transitioned back to supported movement.
    """

    return (
        int(player_state) == 0
        and int(player_y_high) == 1
        and _signed_u8(player_y_speed) == 0
    )


def near_gap_guard(
    radar: dict,
    *,
    trigger_px: int = DEFAULT_NEAR_GAP_PX,
) -> TerrainGapGuard | None:
    """Classify a currently observed near gap for live preemption.

    ``None`` means there is no current positive near-gap observation. The
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
        mode="grounded-rearm" if grounded else "airborne-rearm-commit",
    )


def gap_escape_schedule(
    *,
    hold_frames: int = DEFAULT_GAP_REARM_HOLD_FRAMES,
) -> list[dict[str, int]]:
    """Return the exact re-arm/hold/tail schedule proven by the pit regression.

    From the V25 generation-302 state, ``RIGHT+A+B 4f`` followed by RIGHT+B died.
    Both re-armed jump probes survived; the long variant provided the larger
    landing margin. This schedule matches that long variant:

    ``RIGHT+B 1f -> RIGHT+A+B hold_frames -> RIGHT+B tail``.

    V11-style authority holds the final segment after the explicit schedule, so
    callers can keep the same root frame while the crossing commitment remains
    active without repeatedly restarting the A re-arm sequence.
    """

    if hold_frames <= 0:
        raise ValueError("hold_frames must be > 0")
    return [
        {
            "buttons": int(action_to_nes_buttons(Smb1Action.RIGHT_B)),
            "frames": 1,
        },
        {
            "buttons": int(action_to_nes_buttons(Smb1Action.RIGHT_A_B)),
            "frames": int(hold_frames),
        },
        {
            "buttons": int(action_to_nes_buttons(Smb1Action.RIGHT_B)),
            "frames": 1,
        },
    ]
