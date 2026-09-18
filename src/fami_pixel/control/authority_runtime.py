"""Stable authority-run reset and controller-wrapper scope.

Historical planner versions currently own run-start resets and temporarily replace
``base.set_nes_controller_state`` at several ancestry levels.  Those are control
runtime concerns rather than planner policy.  This module provides explicit,
injected contracts for preserving reset order and LIFO controller-wrapper
restoration while issue #35 moves the active runner away from versioned authority
wrappers.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator


ControllerSetter = Callable[[Any, int, int], Any]
ControllerSetterGetter = Callable[[], ControllerSetter]
ControllerSetterInstaller = Callable[[ControllerSetter], None]
ControllerLayerFactory = Callable[[ControllerSetter], ControllerSetter]
RunResetter = Callable[[], None]


def authority_action_recording_layer(ledger: Any) -> ControllerLayerFactory:
    """Build the historical authority-action recorder as an injected layer.

    The recorder deliberately runs *before* its predecessor setter, matching the
    V27 contract: the final buttons associated with ``frame -> frame+1`` are
    written to the authority ledger when port 0 is set.  Other controller ports
    pass through without becoming SMB1 authority lineage.  A ledger's own
    suspension semantics remain authoritative, so speculative scopes can suppress
    writes without changing this wrapper.
    """

    record = getattr(ledger, "record", None)
    if not callable(record):
        raise TypeError("authority action ledger must provide callable record(frame, buttons)")

    def layer(predecessor: ControllerSetter) -> ControllerSetter:
        def recording_set_controller(core: Any, port: int, buttons: int) -> Any:
            if int(port) == 0:
                record(int(core.frame_count()), int(buttons))
            return predecessor(core, port, buttons)

        return recording_set_controller

    return layer


@dataclass(frozen=True)
class NamedRunReset:
    """One named run-start reset in an authority runtime composition."""

    name: str
    reset: RunResetter

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("run reset name must be non-empty")
        if not callable(self.reset):
            raise TypeError("run reset must be callable")


@dataclass(frozen=True)
class AuthorityRunResetPlan:
    """Ordered, inspectable run-start reset composition.

    Order is behavioral state: versioned authority wrappers currently clear their
    caches, memories, ledgers, and commitments while descending the wrapper chain.
    The plan therefore never sorts or deduplicates steps.  Distinct named steps may
    intentionally target the same underlying state when that is what the historical
    runtime does; extraction can remove redundancy only after separate evidence.
    """

    steps: tuple[NamedRunReset, ...] = ()

    def __post_init__(self) -> None:
        names = tuple(step.name for step in self.steps)
        if len(set(names)) != len(names):
            raise ValueError("run reset step names must be unique")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    def reset_run_state(self) -> None:
        for step in self.steps:
            step.reset()


@dataclass(frozen=True)
class AuthorityRuntimeScope:
    """Explicit authority runtime dependencies for one live run.

    ``reset_run_state`` preserves the configured reset order exactly.  Nested
    ``controller_layer`` scopes wrap the setter visible at entry time and always
    restore that exact predecessor, including when the delegated authority loop
    raises.  This is intentionally policy-free: callers inject concrete resetters
    and wrapper factories.
    """

    get_controller_setter: ControllerSetterGetter
    install_controller_setter: ControllerSetterInstaller
    resetters: tuple[RunResetter, ...] = ()

    def reset_run_state(self) -> None:
        for resetter in self.resetters:
            resetter()

    @contextmanager
    def controller_layer(
        self,
        wrapper_factory: ControllerLayerFactory,
    ) -> Iterator[ControllerSetter]:
        original = self.get_controller_setter()
        wrapped = wrapper_factory(original)
        if not callable(wrapped):
            raise TypeError("controller wrapper factory must return a callable")
        self.install_controller_setter(wrapped)
        try:
            yield wrapped
        finally:
            self.install_controller_setter(original)
