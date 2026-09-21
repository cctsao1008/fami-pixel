#!/usr/bin/env python3
"""Run the V36 Star replay with strict live-authority immutability checks.

This wrapper hardens the end-to-end acceptance gate against a harness-specific
race around scenario restore. ``Debugger::IsExecutionStopped()`` is not a strong
parked-state predicate in Mesen: it is true either for an actual debugger stop or
while the emulator thread is only transiently paused by an internal lock. The
strict gate therefore uses the stronger debugger pause state exposed through
``MesenCore.is_paused()`` (Debugger::_waitForBreakResume) before restoring the
scenario root.

In addition to the outer decision guard, this probe now instruments the native
speculation stages so any authority mutation is attributed to the first exact
operation that caused it: live SMB reads, native root init/capture, root reset,
or native schedule execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

from fami_pixel.adapters.mesen import (
    get_nes_controller_state,
    read_nes_internal_ram,
)

import smb1_v36_native_star_scenario_replay as replay


_original_restore_root = replay._restore_root
_original_native_plan = replay.v36._sync_native_star_plan
_original_read_smb1_state = replay.v36.read_smb1_state
_original_read_smb1_radar = replay.v36.read_smb1_radar
_original_runner_init = replay.v36.NativeSpecRunner.initialize_from_live
_original_runner_capture = replay.v36.NativeSpecRunner.capture_root_from_live
_original_runner_reset = replay.v36.NativeSpecRunner.reset_to_root
_original_runner_schedule = replay.v36.NativeSpecRunner.run_schedule


@dataclass(frozen=True)
class _AuthoritySnapshot:
    frame: int
    paused: bool
    controller: int
    ram: bytes


def _snapshot(core) -> _AuthoritySnapshot:
    return _AuthoritySnapshot(
        frame=int(core.frame_count()),
        paused=bool(core.is_paused()),
        controller=int(get_nes_controller_state(core, 0)),
        ram=read_nes_internal_ram(core),
    )


def _first_ram_difference(before: bytes, after: bytes):
    for index, (lhs, rhs) in enumerate(zip(before, after)):
        if lhs != rhs:
            return index, lhs, rhs
    if len(before) != len(after):
        return -1, len(before), len(after)
    return None


def _assert_stage_unchanged(stage: str, before: _AuthoritySnapshot, after: _AuthoritySnapshot) -> None:
    if before.paused and not after.paused:
        raise RuntimeError(
            f"native V36 stage released persistent debugger pause: stage={stage}"
        )
    if after.frame != before.frame:
        raise RuntimeError(
            "native V36 stage advanced live authority frame: "
            f"stage={stage} before={before.frame} after={after.frame}"
        )
    diff = _first_ram_difference(before.ram, after.ram)
    if diff is not None:
        offset, lhs, rhs = diff
        raise RuntimeError(
            "native V36 stage mutated live authority RAM: "
            f"stage={stage} first_difference=0x{offset:04X} "
            f"before=0x{lhs:02X} after=0x{rhs:02X} frame={before.frame}"
        )
    if after.controller != before.controller:
        raise RuntimeError(
            "native V36 stage mutated live controller state: "
            f"stage={stage} before=0x{before.controller:02X} "
            f"after=0x{after.controller:02X} frame={before.frame}"
        )


def _guard_call(stage: str, core, func, *args, **kwargs):
    before = _snapshot(core)
    result = func(*args, **kwargs)
    after = _snapshot(core)
    _assert_stage_unchanged(stage, before, after)
    return result


def _wait_for_persistent_pause(core, timeout_s: float = 5.0) -> None:
    """Wait until Mesen reports the debugger's persistent paused state."""

    if core.is_paused():
        return

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

    if not core.is_paused():
        raise RuntimeError(
            "strict V36 replay lost persistent debugger pause while restoring the root"
        )

    expected_frame = int(manifest["native_frame"])
    before = _snapshot(core)
    if before.frame != expected_frame:
        raise RuntimeError(
            "strict V36 replay root frame mismatch after debugger pause: "
            f"expected={expected_frame} actual={before.frame}"
        )

    time.sleep(0.030)
    after = _snapshot(core)
    _assert_stage_unchanged("parked-root-guard", before, after)


def _strict_read_smb1_state(core, *args, **kwargs):
    return _guard_call(
        "live-read-smb1-state",
        core,
        _original_read_smb1_state,
        core,
        *args,
        **kwargs,
    )


def _strict_read_smb1_radar(core, *args, **kwargs):
    return _guard_call(
        "live-read-smb1-radar",
        core,
        _original_read_smb1_radar,
        core,
        *args,
        **kwargs,
    )


def _strict_runner_init(self, *args, **kwargs):
    return _guard_call(
        "spec-init-from-live",
        self._core,
        _original_runner_init,
        self,
        *args,
        **kwargs,
    )


def _strict_runner_capture(self, *args, **kwargs):
    return _guard_call(
        "spec-capture-root-from-live",
        self._core,
        _original_runner_capture,
        self,
        *args,
        **kwargs,
    )


def _strict_runner_reset(self, *args, **kwargs):
    return _guard_call(
        "spec-reset-to-root",
        self._core,
        _original_runner_reset,
        self,
        *args,
        **kwargs,
    )


def _strict_runner_schedule(self, buttons, *args, **kwargs):
    return _guard_call(
        "spec-run-schedule",
        self._core,
        _original_runner_schedule,
        self,
        buttons,
        *args,
        **kwargs,
    )


def _strict_native_plan(core, *args, **kwargs):
    """Reject and localize any live-authority mutation during speculation."""

    if not core.is_paused():
        raise RuntimeError("native V36 decision started without a persistent debugger pause")

    before = _snapshot(core)
    result = _original_native_plan(core, *args, **kwargs)
    after = _snapshot(core)
    _assert_stage_unchanged("whole-native-decision", before, after)
    return result


def main() -> int:
    replay._restore_root = _strict_restore_root
    replay.v36.read_smb1_state = _strict_read_smb1_state
    replay.v36.read_smb1_radar = _strict_read_smb1_radar
    replay.v36.NativeSpecRunner.initialize_from_live = _strict_runner_init
    replay.v36.NativeSpecRunner.capture_root_from_live = _strict_runner_capture
    replay.v36.NativeSpecRunner.reset_to_root = _strict_runner_reset
    replay.v36.NativeSpecRunner.run_schedule = _strict_runner_schedule
    replay.v36._sync_native_star_plan = _strict_native_plan
    return replay.main()


if __name__ == "__main__":
    raise SystemExit(main())
