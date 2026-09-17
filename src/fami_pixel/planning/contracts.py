"""Stable callable contracts shared by planner composition layers."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol, Sequence, Any


class PlanSelector(Protocol):
    """Select the next live plan from current authority context.

    Implementations may inspect asynchronous response files, current native frame
    age, and semantic live-radar state.  The contract deliberately does not own
    emulator state or process lifecycle; it is the callable seam between planning
    policy and live control composition.
    """

    def __call__(
        self,
        response_paths: Sequence[Path],
        current_frame: int,
        freshness: int,
        last_applied_generation: int,
        live_radar: Mapping[str, Any],
    ) -> dict | None: ...
