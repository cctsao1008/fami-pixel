#!/usr/bin/env python3
"""Measure Python<->Mesen boundary cost for exact SMB1 control.

This benchmark is intentionally narrow. It does not benchmark planner quality and
it does not change authority semantics. It decomposes the costs that dominate the
current exact-Mesen runtime:

- scalar synchronous frame stepping;
- batched synchronous frame stepping;
- frame counter / SMB1 RAM state / observation / radar reads;
- in-process save-slot versus file-backed save/load round trips;
- a V35-shaped 8 x 4f speculative branch micro-rollout.

The output is a JSON artifact plus a compact console table. Run it on Windows with
the pinned fami-pixel MesenCore.dll. A representative SMB1 save-state may be
supplied with --save-state; otherwise the benchmark uses the post-ROM-load state.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Callable

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import observation_from_state, read_smb1_state
from fami_pixel.games.smb1.radar import read_smb1_radar


SCHEMA = 1
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_BUTTONS = 0x00
DEFAULT_SLOT = 9
BRANCH_COUNT = 8
BRANCH_FRAMES = 4


def _int_auto(value: str) -> int:
    return int(value, 0)


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile for a non-empty finite sample."""

    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 <= float(q) <= 1.0:
        raise ValueError("q must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) for value in ordered):
        raise ValueError("values must be finite")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(q)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_samples(
    durations_ns: list[int],
    *,
    units_per_sample: int,
    unit: str,
) -> dict:
    """Summarize wall-time samples normalized by logical work units."""

    if not durations_ns:
        raise ValueError("durations_ns must not be empty")
    if units_per_sample <= 0:
        raise ValueError("units_per_sample must be > 0")
    if not unit:
        raise ValueError("unit must be non-empty")
    if any(int(value) <= 0 for value in durations_ns):
        raise ValueError("durations must be > 0")

    per_unit_us = [
        float(value) / 1000.0 / float(units_per_sample)
        for value in durations_ns
    ]
    total_ns = sum(int(value) for value in durations_ns)
    total_units = len(durations_ns) * int(units_per_sample)
    seconds = float(total_ns) / 1_000_000_000.0
    return {
        "samples": len(durations_ns),
        "units_per_sample": int(units_per_sample),
        "unit": str(unit),
        "total_units": int(total_units),
        "total_seconds": seconds,
        "throughput_per_second": float(total_units) / seconds,
        "mean_us_per_unit": statistics.fmean(per_unit_us),
        "p50_us_per_unit": percentile(per_unit_us, 0.50),
        "p95_us_per_unit": percentile(per_unit_us, 0.95),
        "p99_us_per_unit": percentile(per_unit_us, 0.99),
        "min_us_per_unit": min(per_unit_us),
        "max_us_per_unit": max(per_unit_us),
    }


def measure(
    operation: Callable[[], None],
    *,
    samples: int,
    warmup: int,
    units_per_sample: int,
    unit: str,
) -> dict:
    if samples <= 0:
        raise ValueError("samples must be > 0")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")

    for _ in range(int(warmup)):
        operation()

    durations: list[int] = []
    for _ in range(int(samples)):
        started = time.perf_counter_ns()
        operation()
        elapsed = time.perf_counter_ns() - started
        durations.append(max(1, int(elapsed)))
    return summarize_samples(
        durations,
        units_per_sample=int(units_per_sample),
        unit=str(unit),
    )


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return float(numerator) / float(denominator)


def derive_comparisons(results: dict[str, dict]) -> dict:
    """Compute directly interpretable ratios without claiming causality."""

    def p50(name: str) -> float | None:
        row = results.get(name)
        if row is None:
            return None
        return float(row["p50_us_per_unit"])

    scalar = p50("step_sync_1")
    batch = p50("step_sync_batch")
    slot_rt = p50("state_slot_roundtrip")
    file_rt = p50("state_file_roundtrip")
    branch_file = p50("branch_8x4_file_scalar")
    branch_slot = p50("branch_8x4_slot_scalar")
    branch_slot_batch = p50("branch_8x4_slot_batch")

    comparisons: dict[str, float] = {}
    pairs = {
        "step_batch_speedup_per_frame": (scalar, batch),
        "slot_roundtrip_speedup_vs_file": (file_rt, slot_rt),
        "branch_slot_speedup_vs_file": (branch_file, branch_slot),
        "branch_slot_batch_speedup_vs_slot_scalar": (branch_slot, branch_slot_batch),
        "branch_slot_batch_speedup_vs_file_scalar": (branch_file, branch_slot_batch),
    }
    for name, (numerator, denominator) in pairs.items():
        if numerator is None or denominator is None:
            continue
        value = _ratio(numerator, denominator)
        if value is not None:
            comparisons[name] = value
    return comparisons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile exact-Mesen Python/native, state-read, and save/restore boundary cost"
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home-boundary-benchmark"))
    parser.add_argument("--save-state", type=Path)
    parser.add_argument("--output", type=Path, default=Path("build/mesen-boundary-benchmark.json"))
    parser.add_argument("--buttons", type=_int_auto, default=DEFAULT_BUTTONS)
    parser.add_argument("--step-timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--batch-frames", type=int, default=4)
    parser.add_argument("--step-samples", type=int, default=240)
    parser.add_argument("--read-samples", type=int, default=400)
    parser.add_argument("--state-samples", type=int, default=40)
    parser.add_argument("--branch-samples", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--slot", type=int, default=DEFAULT_SLOT)
    parser.add_argument("--lookahead", type=int, default=192)
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if args.step_timeout <= 0:
        raise SystemExit("--step-timeout must be > 0")
    if args.batch_frames <= 1:
        raise SystemExit("--batch-frames must be > 1")
    for name in ("step_samples", "read_samples", "state_samples", "branch_samples"):
        if int(getattr(args, name)) <= 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be > 0")
    if args.warmup < 0:
        raise SystemExit("--warmup must be >= 0")
    if args.slot < 0:
        raise SystemExit("--slot must be >= 0")
    if args.lookahead <= 0:
        raise SystemExit("--lookahead must be > 0")


def _timeout_ms(args: argparse.Namespace) -> int:
    return max(1, int(float(args.step_timeout) * 1000.0))


def _restore_baseline(core: MesenCore, *, baseline_slot: int) -> None:
    core.load_state_slot(int(baseline_slot))


def _branch_scalar(
    core: MesenCore,
    *,
    restore: Callable[[], None],
    buttons: int,
    timeout_ms: int,
) -> None:
    for _ in range(BRANCH_COUNT):
        restore()
        for _ in range(BRANCH_FRAMES):
            set_nes_controller_state(core, 0, int(buttons))
            core.step_frame_sync(1, timeout_ms)


def _branch_batch(
    core: MesenCore,
    *,
    restore: Callable[[], None],
    buttons: int,
    timeout_ms: int,
) -> None:
    for _ in range(BRANCH_COUNT):
        restore()
        set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(BRANCH_FRAMES, timeout_ms)


def run(args: argparse.Namespace) -> dict:
    _validate_args(args)
    timeout_ms = _timeout_ms(args)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch_state = output.with_suffix(".scratch.mss")

    core = MesenCore(args.dll)
    try:
        core.initialize_headless(args.home)
        configure_standard_nes_controller(core, port=1)
        if not core.load_rom(args.rom):
            raise SystemExit("LoadRom: FAIL")
        core.initialize_debugger()
        if args.save_state is not None:
            core.load_state_file(args.save_state)

        # Freeze one in-process root so every benchmark phase starts from the
        # same emulator state. This save happens outside measured sections.
        core.save_state_slot(int(args.slot))
        core.save_state_file(scratch_state)

        results: dict[str, dict] = {}

        _restore_baseline(core, baseline_slot=args.slot)
        set_nes_controller_state(core, 0, int(args.buttons))
        results["step_sync_1"] = measure(
            lambda: core.step_frame_sync(1, timeout_ms),
            samples=args.step_samples,
            warmup=args.warmup,
            units_per_sample=1,
            unit="frame",
        )

        _restore_baseline(core, baseline_slot=args.slot)
        set_nes_controller_state(core, 0, int(args.buttons))
        results["step_sync_batch"] = measure(
            lambda: core.step_frame_sync(int(args.batch_frames), timeout_ms),
            samples=max(1, args.step_samples // int(args.batch_frames)),
            warmup=max(1, args.warmup // int(args.batch_frames)),
            units_per_sample=int(args.batch_frames),
            unit="frame",
        )
        results["step_sync_batch"]["batch_frames"] = int(args.batch_frames)

        _restore_baseline(core, baseline_slot=args.slot)
        results["frame_count"] = measure(
            core.frame_count,
            samples=args.read_samples,
            warmup=args.warmup,
            units_per_sample=1,
            unit="call",
        )

        _restore_baseline(core, baseline_slot=args.slot)
        results["read_smb1_state"] = measure(
            lambda: read_smb1_state(core),
            samples=args.read_samples,
            warmup=args.warmup,
            units_per_sample=1,
            unit="read",
        )

        _restore_baseline(core, baseline_slot=args.slot)
        results["read_state_plus_observation"] = measure(
            lambda: observation_from_state(core.frame_count(), read_smb1_state(core)),
            samples=args.read_samples,
            warmup=args.warmup,
            units_per_sample=1,
            unit="observation",
        )

        _restore_baseline(core, baseline_slot=args.slot)
        radar_state = read_smb1_state(core)
        radar_x = int(radar_state.player_absolute_x)
        results["read_smb1_radar"] = measure(
            lambda: read_smb1_radar(
                core,
                player_x=radar_x,
                lookahead_px=int(args.lookahead),
            ),
            samples=args.read_samples,
            warmup=args.warmup,
            units_per_sample=1,
            unit="radar",
        )

        _restore_baseline(core, baseline_slot=args.slot)
        results["state_slot_roundtrip"] = measure(
            lambda: (core.save_state_slot(int(args.slot)), core.load_state_slot(int(args.slot))),
            samples=args.state_samples,
            warmup=min(args.warmup, args.state_samples),
            units_per_sample=1,
            unit="roundtrip",
        )

        _restore_baseline(core, baseline_slot=args.slot)
        results["state_file_roundtrip"] = measure(
            lambda: (core.save_state_file(scratch_state), core.load_state_file(scratch_state)),
            samples=args.state_samples,
            warmup=min(args.warmup, args.state_samples),
            units_per_sample=1,
            unit="roundtrip",
        )

        core.save_state_file(scratch_state)
        results["branch_8x4_file_scalar"] = measure(
            lambda: _branch_scalar(
                core,
                restore=lambda: core.load_state_file(scratch_state),
                buttons=int(args.buttons),
                timeout_ms=timeout_ms,
            ),
            samples=args.branch_samples,
            warmup=min(2, args.warmup),
            units_per_sample=1,
            unit="decision",
        )
        results["branch_8x4_file_scalar"]["simulated_frames_per_decision"] = (
            BRANCH_COUNT * BRANCH_FRAMES
        )

        core.load_state_file(scratch_state)
        core.save_state_slot(int(args.slot))
        results["branch_8x4_slot_scalar"] = measure(
            lambda: _branch_scalar(
                core,
                restore=lambda: core.load_state_slot(int(args.slot)),
                buttons=int(args.buttons),
                timeout_ms=timeout_ms,
            ),
            samples=args.branch_samples,
            warmup=min(2, args.warmup),
            units_per_sample=1,
            unit="decision",
        )
        results["branch_8x4_slot_scalar"]["simulated_frames_per_decision"] = (
            BRANCH_COUNT * BRANCH_FRAMES
        )

        core.load_state_slot(int(args.slot))
        results["branch_8x4_slot_batch"] = measure(
            lambda: _branch_batch(
                core,
                restore=lambda: core.load_state_slot(int(args.slot)),
                buttons=int(args.buttons),
                timeout_ms=timeout_ms,
            ),
            samples=args.branch_samples,
            warmup=min(2, args.warmup),
            units_per_sample=1,
            unit="decision",
        )
        results["branch_8x4_slot_batch"]["simulated_frames_per_decision"] = (
            BRANCH_COUNT * BRANCH_FRAMES
        )

        payload = {
            "schema": SCHEMA,
            "benchmark": "mesen-boundary",
            "host": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python": sys.version.split()[0],
                "cpu_count": os.cpu_count(),
            },
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
                "step_timeout_s": float(args.step_timeout),
                "batch_frames": int(args.batch_frames),
                "lookahead_px": int(args.lookahead),
                "branch_shape": {
                    "candidates": BRANCH_COUNT,
                    "frames_per_candidate": BRANCH_FRAMES,
                },
            },
            "results": results,
            "comparisons": derive_comparisons(results),
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload
    finally:
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _format_row(name: str, row: dict) -> str:
    unit = str(row["unit"])
    p50 = float(row["p50_us_per_unit"])
    p95 = float(row["p95_us_per_unit"])
    p99 = float(row["p99_us_per_unit"])
    rate = float(row["throughput_per_second"])
    return f"{name:30s} {p50:11.2f} {p95:11.2f} {p99:11.2f} {rate:12.1f} {unit}/s"


def print_report(payload: dict, output: Path) -> None:
    print("Mesen boundary benchmark", flush=True)
    print("phase                              p50 us/unit p95 us/unit p99 us/unit   throughput", flush=True)
    for name, row in payload["results"].items():
        print(_format_row(name, row), flush=True)
    if payload["comparisons"]:
        print("\nratios", flush=True)
        for name, value in payload["comparisons"].items():
            print(f"  {name}: {float(value):.3f}x", flush=True)
    print(f"\nJSON: {output.expanduser().resolve()}", flush=True)


def main() -> int:
    args = parse_args()
    payload = run(args)
    print_report(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
