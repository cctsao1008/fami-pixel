#!/usr/bin/env python3
"""Deterministic Mesen probe for the V25/V26 airborne-gap regression.

Use this on an extracted pre-pit or mid-air pit scenario before another full
World 1-1 integration run. It keeps the failed first V26 experiment visible as a
negative control and adds the corrected re-arm escape used by live V26:

    legacy_gap_extend4 : RIGHT+A+B 4f -> RIGHT+B tail
    gap_escape_rearm   : RIGHT+B 1f -> RIGHT+A+B 15f -> RIGHT+B tail

The V25 generation-302 field fixture proved the first branch dies while the
second lands. Keeping both in one exact-Mesen probe prevents future code changes
from laundering the failed 4-frame policy back into live control.
"""

from __future__ import annotations

from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import smb1_trajectory_probe as base

from fami_pixel.games.smb1 import ActionCommand, Smb1Action, TrajectoryPlan


LEGACY_GAP_EXTEND_PLAN = TrajectoryPlan(
    "legacy_gap_extend4",
    (ActionCommand(Smb1Action.RIGHT_A_B, 4),),
    tail_action=Smb1Action.RIGHT_B,
)

GAP_ESCAPE_PLAN = TrajectoryPlan(
    "gap_escape_rearm",
    (
        ActionCommand(Smb1Action.RIGHT_B, 1),
        ActionCommand(Smb1Action.RIGHT_A_B, 15),
    ),
    tail_action=Smb1Action.RIGHT_B,
)

base.PLANS = (LEGACY_GAP_EXTEND_PLAN, GAP_ESCAPE_PLAN, *base.PLANS)
# Keep supervisor children on this wrapper so both regression candidates survive
# the --worker boundary.
base.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(base.main())
