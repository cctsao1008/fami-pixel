#!/usr/bin/env python3
"""Decompose the exact Mesen debugger PPU-frame step latency.

This probe exists to answer one narrow performance question before changing the
runtime architecture: where does the current ~14-16 ms scalar synchronous step
actually go?

It uses the probe-only native export FamiPixelProfileStepFrame from the matching
cctsao1008/MesenCE probe branch and reports four native timing buckets:

- step_call: debugger->Step() call duration;
- frame_wait: wait after Step returns until PpuFrameDone is observed;
- stop_wait: wait after the frame witness until IsExecutionStopped();
- total: full native synchronous step duration.

The production FamiPixelStepFrame export is not changed by this probe. Results
are collected for scalar and batched stepping under both normal pacing and
MaximumSpeed so the existing #40 measurements can be explained rather than
replaced by speculation.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
from pathlib import Path
import statistics

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)


EMULATION_FLAG_MAXIMUM_SPEED = 0x04
DEFAULT_SLOT = 9


def _int_auto(value: str) -> int:
    return int(value, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decompose exact Mesen synchronous PPU-frame step latency"
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home-step-timing-probe"))
    parser.add_argument("--save-state", type=Path)
    parser.add_argument("--output", type=Path, default=Path("build/mesen-step-timing-probe.json"))
    parser.add_argument("--buttons", type=_int_auto, default=0x00)
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--batch-frames", type=int, default=4)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--slot", type=int, default=DEFAULT_SLOT)
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if int(args.samples) <= 0:
        raise SystemExit("--samples must be > 0")
    if int(args.warmup) < 0:
        raise SystemExit("--warmup must be >= 0")
    if int(args.batch_frames) <= 1:
        raise SystemExit("--batch-frames must be > 1")
    if float(args.step_timeout) <= 0:
        raise SystemExit("--step-timeout must be > 0")
    if int(args.slot) < 0:
        raise SystemExit("--slot must be >= 0")


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("values must not be empty")
    if not 0.0 <= float(q) <= 1.0:
        raise ValueError("q must be in [0, 1]")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(q)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summary_ns(values: list[int], *, divisor: int = 1) -> dict[str, float | int]:
    if not values:
        raise ValueError("values must not be empty")
    if int(divisor) <= 0:
        raise ValueError("divisor must be > 0")
    normalized_us = [float(value) / 1000.0 / float(divisor) for value in values]
    return {
        "samples": len(values),
        "mean_us_per_frame": statistics.fmean(normalized_us),
        "p50_us_per_frame": _percentile(normalized_us, 0.50),
        "p95_us_per_frame": _percentile(normalized_us, 0.95),
        "p99_us_per_frame": _percentile(normalized_us, 0.99),
        "min_us_per_frame": min(normalized_us),
        "max_us_per_frame": max(normalized_us),
    }


def _bind_profile_step(core: MesenCore):
    name = "FamiPixelProfileStepFrame"
    if not core.has_export(name):
        raise RuntimeError(
            f"MesenCore.dll does not export {name}; rebuild the DLL from the "
            "matching cctsao1008/MesenCE probe branch before running this probe."
        )
    func = core._dll.FamiPixelProfileStepFrame  # probe-only native ABI
    u64p = ctypes.POINTER(ctypes.c_uint64)
    func.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        u64p,
        u64p,
        u64p,
        u64p,
    ]
    func.restype = ctypes.c_int32
    return func


def _set_maximum_speed(core: MesenCore, enabled: bool) -> None:
    if not core.has_export("SetEmulationFlag"):
        raise RuntimeError("MesenCore.dll does not export SetEmulationFlag")
    setter = core._dll.SetEmulationFlag  # probe-only verified stock ABI
    setter.argtypes = [ctypes.c_int, ctypes.c_bool]
    setter.restype = None
    setter(int(EMULATION_FLAG_MAXIMUM_SPEED), bool(enabled))


def _profile_one(func, *, frames: int, timeout_ms: int) -> dict[str, int]:
    step_call = ctypes.c_uint64(0)
    frame_wait = ctypes.c_uint64(0)
    stop_wait = ctypes.c_uint64(0)
    total = ctypes.c_uint64(0)
    status = int(
        func(
            int(frames),
            int(timeout_ms),
            ctypes.byref(step_call),
            ctypes.byref(frame_wait),
            ctypes.byref(stop_wait),
            ctypes.byref(total),
        )
    )
    row = {
        "status": status,
        "step_call_ns": int(step_call.value),
        "frame_wait_ns": int(frame_wait.value),
        "stop_wait_ns": int(stop_wait.value),
        "total_ns": int(total.value),
    }
    if status != 0:
        raise RuntimeError(f"FamiPixelProfileStepFrame failed: {row}")
    partition = row["step_call_ns"] + row["frame_wait_ns"] + row["stop_wait_ns"]
    if partition != row["total_ns"]:
        raise RuntimeError(
            "native timing partition mismatch: "
            f"step+frame+stop={partition}, total={row['total_ns']}"
        )
    return row


def _profile_phase(
    core: MesenCore,
    func,
    *,
    frames: int,
    timeout_ms: int,
    samples: int,
    warmup: int,
    buttons: int,
) -> dict:
    set_nes_controller_state(core, 0, int(buttons))
    for _ in range(int(warmup)):
        _profile_one(func, frames=int(frames), timeout_ms=int(timeout_ms))

    rows = [
        _profile_one(func, frames=int(frames), timeout_ms=int(timeout_ms))
        for _ in range(int(samples))
    ]
    fields = ("step_call_ns", "frame_wait_ns", "stop_wait_ns", "total_ns")
    summary = {
        field.removesuffix("_ns"): _summary_ns(
            [int(row[field]) for row in rows],
            divisor=int(frames),
        )
        for field in fields
    }
    total_sum = sum(int(row["total_ns"]) for row in rows)
    shares = {}
    if total_sum > 0:
        for field in ("step_call_ns", "frame_wait_ns", "stop_wait_ns"):
            shares[field.removesuffix("_ns")] = (
                sum(int(row[field]) for row in rows) / float(total_sum)
            )
    return {
        "frames_per_call": int(frames),
        "summary": summary,
        "aggregate_time_share": shares,
    }


def _profile_mode(
    core: MesenCore,
    func,
    args: argparse.Namespace,
    *,
    maximum_speed: bool,
    timeout_ms: int,
) -> dict:
    _set_maximum_speed(core, bool(maximum_speed))
    core.load_state_slot(int(args.slot))
    scalar = _profile_phase(
        core,
        func,
        frames=1,
        timeout_ms=timeout_ms,
        samples=int(args.samples),
        warmup=int(args.warmup),
        buttons=int(args.buttons),
    )

    core.load_state_slot(int(args.slot))
    batch_frames = int(args.batch_frames)
    batch = _profile_phase(
        core,
        func,
        frames=batch_frames,
        timeout_ms=timeout_ms,
        samples=max(1, int(args.samples) // batch_frames),
        warmup=max(1, int(args.warmup) // batch_frames),
        buttons=int(args.buttons),
    )
    return {"scalar": scalar, "batch": batch}


def run(args: argparse.Namespace) -> dict:
    _validate_args(args)
    timeout_ms = max(1, int(float(args.step_timeout) * 1000.0))
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    core = MesenCore(args.dll)
    try:
        core.initialize_headless(args.home)
        configure_standard_nes_controller(core, port=1)
        if not core.load_rom(args.rom):
            raise SystemExit("LoadRom: FAIL")
        core.initialize_debugger()
        if args.save_state is not None:
            core.load_state_file(args.save_state)
        core.save_state_slot(int(args.slot))

        profile_step = _bind_profile_step(core)
        normal = _profile_mode(
            core,
            profile_step,
            args,
            maximum_speed=False,
            timeout_ms=timeout_ms,
        )
        maximum = _profile_mode(
            core,
            profile_step,
            args,
            maximum_speed=True,
            timeout_ms=timeout_ms,
        )

        payload = {
            "schema": 1,
            "probe": "mesen-step-timing",
            "mesen": {
                "dll": str(Path(args.dll).expanduser().resolve()),
                "version": int(core.version()),
                "build_date": core.build_date(),
            },
            "input": {
                "rom": str(Path(args.rom).expanduser().resolve()),
                "save_state": None
                if args.save_state is None
                else str(Path(args.save_state).expanduser().resolve()),
                "buttons": int(args.buttons),
                "samples": int(args.samples),
                "warmup": int(args.warmup),
                "batch_frames": int(args.batch_frames),
                "step_timeout_s": float(args.step_timeout),
            },
            "normal": normal,
            "maximum_speed": maximum,
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload
    finally:
        try:
            _set_maximum_speed(core, False)
        except Exception:
            pass
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _print_phase(mode: str, phase: str, row: dict) -> None:
    frames = int(row["frames_per_call"])
    print(f"\n{mode} / {phase} ({frames} frame(s)/call)")
    print("segment       p50 us/frame  p95 us/frame  p99 us/frame   aggregate share")
    shares = row["aggregate_time_share"]
    for segment in ("step_call", "frame_wait", "stop_wait", "total"):
        summary = row["summary"][segment]
        share_text = "-"
        if segment in shares:
            share_text = f"{100.0 * float(shares[segment]):6.2f}%"
        print(
            f"{segment:12s} "
            f"{float(summary['p50_us_per_frame']):12.2f} "
            f"{float(summary['p95_us_per_frame']):12.2f} "
            f"{float(summary['p99_us_per_frame']):12.2f} "
            f"{share_text:>16s}"
        )


def print_report(payload: dict, output: Path) -> None:
    print("Mesen exact step timing probe")
    for mode_key, mode_name in (("normal", "normal"), ("maximum_speed", "MaximumSpeed")):
        mode = payload[mode_key]
        _print_phase(mode_name, "scalar", mode["scalar"])
        _print_phase(mode_name, "batch", mode["batch"])
    print(f"\nJSON: {output.expanduser().resolve()}")


def main() -> int:
    args = parse_args()
    payload = run(args)
    print_report(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
