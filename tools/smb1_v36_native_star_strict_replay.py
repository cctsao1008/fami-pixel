#!/usr/bin/env python3
"""Run the V36 Star replay with strict live-authority immutability checks.

This wrapper hardens the end-to-end acceptance gate against one harness-specific
race: immediately after loading a scenario state, the live Mesen instance can
still be free-running until the first debugger-synchronous step parks it. A
native speculative decision made during that window can be correctly rooted at
the saved state while the live authority advances before commit.

The strict gate therefore:

1. parks debugger execution once, then restores the exact scenario root again;
2. requires the debugger to remain stopped at that restored root; and
3. verifies frame count, full 2 KiB NES RAM, and live controller state are
   unchanged across every native V36 decision call.

The underlying policy, commit loop, collection proof, and latency reporting are
all provided by ``smb1_v36_native_star_scenario_replay`` unchanged.
"""

from __future__ import annotations

from fami_pixel.adapters.mesen import (
    get_nes_controller_state,
    read_nes_internal_ram,
)

import smb1_v36_native_star_scenario_replay as replay


_original_restore_root = replay._restore_root
_original_native_plan = replay.v36._sync_native_star_plan


def _strict_restore_root(core, state_file, manifest) -> None:
    """Restore only after forcing the live debugger into its parked state."""

    _original_restore_root(core, state_file, manifest)

    if not core.is_execution_stopped():
        # The first synchronous debugger step establishes the same parked state
        # that normal authority execution has between committed frames. It may
        # advance the temporary live state by one period, so immediately restore
        # the exact requested scenario root afterwards.
        core.step_frame_sync(1, timeout_ms=5000)
        _original_restore_root(core, state_file, manifest)

    if not core.is_execution_stopped():
        raise RuntimeError(
            "strict V36 replay could not park debugger execution at the restored root"
        )

    expected_frame = int(manifest["native_frame"])
    actual_frame = int(core.frame_count())
    if actual_frame != expected_frame:
        raise RuntimeError(
            "strict V36 replay root frame mismatch after debugger park: "
            f"expected={expected_frame} actual={actual_frame}"
        )


def _strict_native_plan(core, *args, **kwargs):
    """Reject any live-authority mutation caused by speculative decision work."""

    frame_before = int(core.frame_count())
    ram_before = read_nes_internal_ram(core)
    controller_before = int(get_nes_controller_state(core, 0))

    result = _original_native_plan(core, *args, **kwargs)

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
