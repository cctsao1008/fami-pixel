"""Stable memory for the schedule currently selected by live authority.

This module owns only control-plane state.  It does not decide which plan wins,
and it does not evaluate emulator transitions.  The stored schedule is the exact
schedule already selected by the authority-side planner and can be projected
from a later frame to warm-start asynchronous counterfactual search.
"""

from __future__ import annotations

from typing import Callable, Mapping


Schedule = list[dict]
ScheduleProjector = Callable[..., Schedule]


class AuthorityPlanMemory:
    """Remember one authoritative plan schedule and project its remaining phase."""

    def __init__(self) -> None:
        self._plan: dict | None = None

    def clear(self) -> None:
        self._plan = None

    def remember(self, plan: Mapping | None) -> bool:
        """Store a valid selected plan, returning whether memory changed."""

        if not plan:
            return False
        schedule = plan.get("schedule") or ()
        if not schedule:
            return False
        try:
            root_frame = int(plan["root_frame"])
            copied_schedule = [dict(segment) for segment in schedule]
            for segment in copied_schedule:
                int(segment["buttons"])
                if int(segment["frames"]) <= 0:
                    return False
        except (KeyError, TypeError, ValueError):
            return False

        self._plan = {
            "root_frame": root_frame,
            "candidate": str(plan.get("candidate") or "unknown"),
            "schedule": copied_schedule,
        }
        return True

    @property
    def snapshot(self) -> dict | None:
        """Return an isolated copy suitable for telemetry/request annotation."""

        if self._plan is None:
            return None
        return {
            "root_frame": int(self._plan["root_frame"]),
            "candidate": str(self._plan["candidate"]),
            "schedule": [dict(segment) for segment in self._plan["schedule"]],
        }

    def continuation(
        self,
        frame: int,
        *,
        frames: int,
        projector: ScheduleProjector,
    ) -> Schedule:
        """Project the remembered schedule from ``frame`` for ``frames`` frames."""

        if self._plan is None:
            return []
        age = int(frame) - int(self._plan["root_frame"])
        if age < 0:
            return []
        return list(
            projector(
                [dict(segment) for segment in self._plan["schedule"]],
                start_age=age,
                frames=int(frames),
            )
        )
