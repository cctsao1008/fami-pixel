#!/usr/bin/env python3
"""Run the V36 Star replay with strict live-authority immutability checks.

This wrapper hardens the end-to-end acceptance gate against a harness-specific
race around scenario restore. ``Debugger::IsExecutionStopped()`` is not a strong
parked-state predicate in Mesen: it is true either for an actual debugger stop or
while the emulator thread is only transiently paused by an internal lock. The
strict gate therefore uses the stronger debugger pause state exposed through
``MesenCore.is_paused()`` (Debugger::_waitForBreakResume) before restoring the
scenario root.

The strict gate:

1. establishes a persistent debugger pause before loading the scenario state;
2. restores the exact scenario root while that pause is held;
3. verifies the restored root remains frame/RAM-stable for a short guard window;
4. verifies frame count, full 2 KiB NES RAM, and live controller state are
   unchanged across every native V36 decision call.

The underlying policy, commit loop, collection proof, and latency reporting are
all provided by ``smb1_v36_native_star_scenario_replay`` unchanged.
"""

from __future__ import annotations

import time

from fami_pixel.adapters.mesen import (
    get_nes_controller_state,
    read_nes_internal_ram,
)

import smb1_v36_native_star_scenario_replay as replay


_original_restore_root = replay._restore_root
_original_native_plan = replay.v36._sync_native_star_plan


def _wait_for_persistent_pause(core, timeout_s: float = 5.0) -> None:
    """Wait until Mesen reports the debugger's persistent paused state."""

    if core.is_paused():
        return

    # Emulator::Pause() maps to Debugger::Step(..., BreakSource::Pause) while the
    # debugger is active. The temporary state may advance slightly, which is fine:
    # the exact scenario root is restored only after this pause is established.
    core.pause()
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        if core.is_paused():
            return
        time.sleep(0.001)
    raise RuntimeError("strict V36 replay could not establish a persistent debugger pause")


def _strict_restore_root(core, state_file, manifest) -> None:
    """Restore the scenario only while the debugger is persistently paused."""

    _wait_for_persistent_pause(core)
    _original_restore_root(core, state_file, manifest)

    # ``is_execution_stopped`` is intentionally not used here. Mesen implements
    # it as ``_executionStopped || IsThreadPaused()``, and the second term can be
    # true only transiently while an internal lock is held. ``is_paused`` maps to
    # Debugger::IsPaused(), i.e. the persistent ``_waitForBreakResume`` gate.
    if not core.is_paused():
        raise RuntimeError(
            "strict V36 replay lost persistent debugger pause while restoring the root"
        )

    expected_frame = int(manifest["native_frame"])
    frame_before = int(core.frame_count())
    ram_before = read_nes_internal_ram(core)
    if frame_before != expected_frame:
        raise RuntimeError(
            "strict V36 replay root frame mismatch after debugger pause: "
            f"expected={expected_frame} actual={frame_before}"
        )

    # Prove the restored root is really parked rather than observing a transient
    # lock stop. At NTSC speed this window spans well over one frame period.
    time.sleep(0.030)
    frame_after = int(core.frame_count())
    ram_after = read_nes_internal_ram(core)
    if frame_after != frame_before:
        raise RuntimeError(
            "strict V36 replay root was not persistently parked: "
            f"before={frame_before} after={frame_after}"
        )
    if ram_after != ram_before:
        first = next(
            (
                index
                for index, (before, after) in enumerate(zip(ram_before, ram_after))
                if before != after
            ),
            -1,
        )
        raise RuntimeError(
            "strict V36 replay root RAM changed while supposedly parked: "
            f"first_difference=0x{first:04X}"
        )


def _strict_native_plan(core, *args, **kwargs):
    """Reject any live-authority mutation caused by speculative decision work."""

    if not core.is_paused():
        raise RuntimeError("native V36 decision started without a persistent debugger pause")

    frame_before = int(core.frame_count())
    ram_before = read_nes_internal_ram(core)
    controller_before = int(get_nes_controller_state(core, 0))

    result = _original_native_plan(core, *args, **kwargs)

    if not core.is_paused():
        raise RuntimeError("native V36 decision released the persistent debugger pause")

    frame_after = int(core.frame_count())
    ram_after = read_nes_internal_ram(core)
    controller_after = int(get_nes_controller_state(core, 0))

    if frame_after != frame_before:
        raise RuntimeError(
            "native V36 decision advanced live authority frame: "
            f"before={frame_before} after={frame_after}"
        )
    if ram_after != ram_before:
        first = next(
            (
                index
                for index, (before, after) in enumerate(zip(ram_before, ram_after))
                if before != after
            ),
            -1,
        )
        raise RuntimeError(
            "native V36 decision mutated live authority RAM: "
            f"first_difference=0x{first:04X}"
        )
    if controller_after != controller_before:
        raise RuntimeError(
            "native V36 decision mutated live controller state: "
            f"before=0x{controller_before:02X} after=0x{controller_after:02X}"
        )

    return result


def main() -> int:
    replay._restore_root = _strict_restore_root
    replay.v36._sync_native_star_plan = _strict_native_plan
    return replay.main()


if __name__ == "__main__":
    raise SystemExit(main())
