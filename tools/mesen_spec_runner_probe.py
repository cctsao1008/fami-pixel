#!/usr/bin/env python3
"""Validate and benchmark the sequential native Mesen speculative runner v0.

This probe compares the existing debugger-authoritative one-frame path against a
separate native speculative Emulator that restores an in-memory root and calls
IConsole::RunFrame() directly. It is intentionally narrow:

- whole 2 KiB NES internal RAM equality after every frame;
- controller-byte and frame-count equality;
- two representative 4-frame input schedules;
- a sequential 8 x 4-frame shaped timing probe with a RAM witness per frame.

The speculative exports are probe/feature ABI from cctsao1008/MesenCE and are
not part of the normal fami-pixel adapter yet.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
from pathlib import Path
import statistics
import time

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    get_nes_controller_state,
    read_nes_internal_ram,
    set_nes_controller_state,
)


EMULATION_FLAG_MAXIMUM_SPEED = 0x04
DEFAULT_SLOT = 9

# Representative exact 4-frame schedules used for equivalence.
EQUIVALENCE_SCHEDULES: dict[str, tuple[int, ...]] = {
    "right_b_then_right_ab": (0x82, 0x83, 0x83, 0x83),
    "release_then_left_b": (0x00, 0x00, 0x42, 0x42),
}

# Cardinality/duration-shaped 8 x 4f workload for sequential timing. These are
# not claimed to be the full runtime candidate generator; the purpose here is
# to measure the native reset/run/witness floor before integrating policy code.
BENCH_SCHEDULES: tuple[tuple[int, ...], ...] = (
    (0x82, 0x82, 0x82, 0x82),
    (0x83, 0x83, 0x83, 0x83),
    (0x00, 0x00, 0x00, 0x00),
    (0x42, 0x42, 0x42, 0x42),
    (0x82, 0x83, 0x83, 0x83),
    (0x00, 0x00, 0x42, 0x42),
    (0x80, 0x82, 0x82, 0x82),
    (0x02, 0x82, 0x83, 0x83),
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validate and benchmark the native sequential Mesen speculative runner"
    )
    p.add_argument("rom", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-spec-runner-probe"))
    p.add_argument("--save-state", type=Path)
    p.add_argument("--slot", type=int, default=DEFAULT_SLOT)
    p.add_argument("--samples", type=int, default=30)
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument(
        "--output",
        type=Path,
        default=Path("build/mesen-spec-runner-probe.json"),
    )
    return p.parse_args()


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("values must not be empty")
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    w = pos - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def _summary_ms(values_ns: list[int]) -> dict[str, float | int]:
    values_ms = [float(v) / 1_000_000.0 for v in values_ns]
    return {
        "samples": len(values_ns),
        "mean_ms": statistics.fmean(values_ms),
        "p50_ms": _percentile(values_ms, 0.50),
        "p95_ms": _percentile(values_ms, 0.95),
        "p99_ms": _percentile(values_ms, 0.99),
        "min_ms": min(values_ms),
        "max_ms": max(values_ms),
    }


def _set_maximum_speed(core: MesenCore, enabled: bool) -> None:
    if not core.has_export("SetEmulationFlag"):
        raise RuntimeError("MesenCore.dll does not export SetEmulationFlag")
    setter = core._dll.SetEmulationFlag
    setter.argtypes = [ctypes.c_int, ctypes.c_bool]
    setter.restype = None
    setter(int(EMULATION_FLAG_MAXIMUM_SPEED), bool(enabled))


class SpecApi:
    def __init__(self, core: MesenCore) -> None:
        required = (
            "FamiPixelSpecInitFromLive",
            "FamiPixelSpecCaptureRootFromLive",
            "FamiPixelSpecResetToRoot",
            "FamiPixelSpecSetNesControllerState",
            "FamiPixelSpecRunFrames",
            "FamiPixelSpecGetNesControllerState",
            "FamiPixelSpecReadNesInternalRam",
            "FamiPixelSpecGetFrameCount",
            "FamiPixelSpecRelease",
        )
        missing = [name for name in required if not core.has_export(name)]
        if missing:
            raise RuntimeError(
                "MesenCore.dll is missing native spec-runner exports: "
                + ", ".join(missing)
            )

        dll = core._dll
        self._init = dll.FamiPixelSpecInitFromLive
        self._init.argtypes = []
        self._init.restype = ctypes.c_int32

        self._capture = dll.FamiPixelSpecCaptureRootFromLive
        self._capture.argtypes = []
        self._capture.restype = ctypes.c_int32

        self._reset = dll.FamiPixelSpecResetToRoot
        self._reset.argtypes = []
        self._reset.restype = ctypes.c_int32

        self._set = dll.FamiPixelSpecSetNesControllerState
        self._set.argtypes = [ctypes.c_uint32, ctypes.c_uint8]
        self._set.restype = ctypes.c_int32

        self._run = dll.FamiPixelSpecRunFrames
        self._run.argtypes = [ctypes.c_uint32]
        self._run.restype = ctypes.c_int32

        self._get_controller = dll.FamiPixelSpecGetNesControllerState
        self._get_controller.argtypes = [ctypes.c_uint32]
        self._get_controller.restype = ctypes.c_int32

        self._read_ram = dll.FamiPixelSpecReadNesInternalRam
        self._read_ram.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
        ]
        self._read_ram.restype = ctypes.c_int32

        self._frame_count = dll.FamiPixelSpecGetFrameCount
        self._frame_count.argtypes = []
        self._frame_count.restype = ctypes.c_uint32

        self._release = dll.FamiPixelSpecRelease
        self._release.argtypes = []
        self._release.restype = None

    @staticmethod
    def _check(name: str, status: int) -> None:
        if int(status) != 0:
            raise RuntimeError(f"{name} failed with status={int(status)}")

    def init_from_live(self) -> None:
        self._check("FamiPixelSpecInitFromLive", self._init())

    def capture_root_from_live(self) -> None:
        self._check("FamiPixelSpecCaptureRootFromLive", self._capture())

    def reset_to_root(self) -> None:
        self._check("FamiPixelSpecResetToRoot", self._reset())

    def set_buttons(self, buttons: int, port: int = 0) -> None:
        self._check(
            "FamiPixelSpecSetNesControllerState",
            self._set(int(port), int(buttons)),
        )

    def run_frames(self, count: int = 1) -> None:
        self._check("FamiPixelSpecRunFrames", self._run(int(count)))

    def controller(self, port: int = 0) -> int:
        value = int(self._get_controller(int(port)))
        if value < 0:
            raise RuntimeError("FamiPixelSpecGetNesControllerState failed")
        return value

    def ram(self) -> bytes:
        buf = (ctypes.c_uint8 * 0x800)()
        self._check(
            "FamiPixelSpecReadNesInternalRam",
            self._read_ram(0, buf, len(buf)),
        )
        return bytes(buf)

    def frame_count(self) -> int:
        return int(self._frame_count())

    def release(self) -> None:
        self._release()


def _first_difference(a: bytes, b: bytes) -> dict[str, int] | None:
    if len(a) != len(b):
        return {"offset": -1, "live": len(a), "spec": len(b)}
    for i, (av, bv) in enumerate(zip(a, b)):
        if av != bv:
            return {"offset": i, "live": av, "spec": bv}
    return None


def _equivalence_case(
    core: MesenCore,
    spec: SpecApi,
    *,
    slot: int,
    timeout_ms: int,
    name: str,
    schedule: tuple[int, ...],
) -> dict:
    core.load_state_slot(int(slot))
    spec.reset_to_root()

    frames: list[dict] = []
    for index, buttons in enumerate(schedule, start=1):
        set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(1, timeout_ms=timeout_ms)
        live_ram = read_nes_internal_ram(core)
        live_controller = get_nes_controller_state(core, 0)
        live_frame_count = core.frame_count()

        spec.set_buttons(int(buttons), 0)
        spec.run_frames(1)
        spec_ram = spec.ram()
        spec_controller = spec.controller(0)
        spec_frame_count = spec.frame_count()

        ram_diff = _first_difference(live_ram, spec_ram)
        row = {
            "frame": index,
            "buttons": int(buttons),
            "live_frame_count": int(live_frame_count),
            "spec_frame_count": int(spec_frame_count),
            "live_controller": int(live_controller),
            "spec_controller": int(spec_controller),
            "ram_equal": ram_diff is None,
            "ram_first_difference": ram_diff,
        }
        frames.append(row)
        if (
            ram_diff is not None
            or live_controller != spec_controller
            or live_frame_count != spec_frame_count
        ):
            return {"name": name, "pass": False, "frames": frames}

    return {"name": name, "pass": True, "frames": frames}


def _benchmark_8x4(spec: SpecApi, samples: int) -> dict:
    totals: list[int] = []
    reset_totals: list[int] = []
    run_totals: list[int] = []
    witness_totals: list[int] = []

    for _ in range(samples):
        t_decision0 = time.perf_counter_ns()
        reset_ns = 0
        run_ns = 0
        witness_ns = 0
        for schedule in BENCH_SCHEDULES:
            t0 = time.perf_counter_ns()
            spec.reset_to_root()
            t1 = time.perf_counter_ns()
            reset_ns += t1 - t0

            for buttons in schedule:
                spec.set_buttons(buttons)
                t2 = time.perf_counter_ns()
                spec.run_frames(1)
                t3 = time.perf_counter_ns()
                run_ns += t3 - t2

                t4 = time.perf_counter_ns()
                spec.ram()
                t5 = time.perf_counter_ns()
                witness_ns += t5 - t4

        t_decision1 = time.perf_counter_ns()
        totals.append(t_decision1 - t_decision0)
        reset_totals.append(reset_ns)
        run_totals.append(run_ns)
        witness_totals.append(witness_ns)

    return {
        "shape": "8x4f sequential; root reset per candidate; 2KiB RAM witness per frame",
        "decision_total": _summary_ms(totals),
        "root_reset_total": _summary_ms(reset_totals),
        "direct_runframe_total": _summary_ms(run_totals),
        "ram_witness_total": _summary_ms(witness_totals),
    }


def run(args: argparse.Namespace) -> dict:
    if args.samples <= 0:
        raise SystemExit("--samples must be > 0")
    timeout_ms = max(1, int(float(args.step_timeout) * 1000.0))
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    core = MesenCore(args.dll)
    spec: SpecApi | None = None
    try:
        core.initialize_headless(args.home)
        configure_standard_nes_controller(core, port=1)
        if not core.load_rom(args.rom):
            raise SystemExit("LoadRom: FAIL")
        core.initialize_debugger()
        if args.save_state is not None:
            core.load_state_file(args.save_state)
        _set_maximum_speed(core, True)

        core.save_state_slot(int(args.slot))
        spec = SpecApi(core)
        spec.init_from_live()

        equivalence = [
            _equivalence_case(
                core,
                spec,
                slot=int(args.slot),
                timeout_ms=timeout_ms,
                name=name,
                schedule=schedule,
            )
            for name, schedule in EQUIVALENCE_SCHEDULES.items()
        ]
        equivalence_pass = all(bool(row["pass"]) for row in equivalence)

        # Restore the exact original live root before timing the isolated spec path.
        core.load_state_slot(int(args.slot))
        spec.capture_root_from_live()
        benchmark = _benchmark_8x4(spec, int(args.samples)) if equivalence_pass else None

        payload = {
            "schema": 1,
            "probe": "mesen-native-spec-runner-v0",
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
                "slot": int(args.slot),
                "samples": int(args.samples),
            },
            "equivalence_pass": equivalence_pass,
            "equivalence": equivalence,
            "benchmark": benchmark,
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload
    finally:
        if spec is not None:
            try:
                spec.release()
            except Exception:
                pass
        try:
            _set_maximum_speed(core, False)
        except Exception:
            pass
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def print_report(payload: dict, output: Path) -> None:
    print("Mesen native speculative runner v0 probe")
    print(f"equivalence: {'PASS' if payload['equivalence_pass'] else 'FAIL'}")
    for case in payload["equivalence"]:
        status = "PASS" if case["pass"] else "FAIL"
        print(f"  {case['name']}: {status}")
        if not case["pass"]:
            print(f"    last frame: {case['frames'][-1]}")

    bench = payload.get("benchmark")
    if bench is not None:
        print("\n8 x 4f sequential native speculative benchmark")
        for key in (
            "decision_total",
            "root_reset_total",
            "direct_runframe_total",
            "ram_witness_total",
        ):
            row = bench[key]
            print(
                f"  {key:24s} "
                f"p50={row['p50_ms']:.3f} ms  "
                f"p95={row['p95_ms']:.3f} ms  "
                f"p99={row['p99_ms']:.3f} ms"
            )
    print(f"\nJSON: {output.expanduser().resolve()}")


def main() -> int:
    args = parse_args()
    payload = run(args)
    print_report(payload, args.output)
    return 0 if payload["equivalence_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
