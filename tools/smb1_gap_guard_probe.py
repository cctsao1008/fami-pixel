#!/usr/bin/env python3
"""Deterministic Mesen probe for the V25/V26 airborne-gap regression.

Use this on an extracted pre-pit or mid-air pit scenario before another full
World 1-1 integration run.  It preserves the normal trajectory-probe candidates
and adds the exact V26 bounded prefix:

    RIGHT+A+B 4f -> RIGHT+B tail

The underlying probe still treats hard HORIZON as UNRESOLVED and Mesen as the
transition/death/landing authority.
"""

from __future__ import annotations

from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import smb1_trajectory_probe as base

from fami_pixel.games.smb1 import ActionCommand, Smb1Action, TrajectoryPlan


GAP_EXTEND_PLAN = TrajectoryPlan(
    "gap_extend4",
    (ActionCommand(Smb1Action.RIGHT_A_B, 4),),
    tail_action=Smb1Action.RIGHT_B,
)

base.PLANS = (GAP_EXTEND_PLAN, *base.PLANS)
# Keep supervisor children on this wrapper so the extra candidate survives the
# --worker boundary.
base.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(base.main())
