"""Explicit process-local delegate slots for live planner composition.

Historical SMB1 runners accumulated behavior by assigning functions into globals
owned by earlier example modules.  A delegate slot keeps the same cheap
process-local dispatch model while making ownership and replacement explicit.
It does not add synchronization, persistence, or policy of its own.
"""

from __future__ import annotations

from fami_pixel.planning import PlanSelector


class PlanDelegateSlot:
    """Hold one replaceable plan-selector dependency with an explicit default."""

    def __init__(self, name: str, default: PlanSelector) -> None:
        if not name:
            raise ValueError("name must be non-empty")
        if not callable(default):
            raise TypeError("default selector must be callable")
        self.name = str(name)
        self._default = default
        self._selector = default

    @property
    def selector(self) -> PlanSelector:
        return self._selector

    @property
    def is_default(self) -> bool:
        return self._selector is self._default

    def install(self, selector: PlanSelector) -> None:
        if not callable(selector):
            raise TypeError("selector must be callable")
        self._selector = selector

    def reset(self) -> None:
        self._selector = self._default

    def select(
        self,
        response_paths,
        current_frame: int,
        freshness: int,
        last_applied_generation: int,
        live_radar: dict,
    ) -> dict | None:
        return self._selector(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
            live_radar,
        )
