#!/usr/bin/env python3
"""Validate and benchmark the sequential native Mesen speculative runner v0.

This probe compares the existing debugger-authoritative PPU-frame stepping path
against a separate native speculative Emulator. The speculative side runs each
variable-input schedule continuously and captures witnesses at the exact same
fixed PPU-period boundaries used by Debugger::Step(StepType::PpuFrame).

This distinction matters: NesConsole::RunFrame() stops when the PPU frame count
changes, which is not equivalent to advancing one full PPU-frame period from an
arbitrary current phase.

The gate is intentionally narrow:

- whole 2 KiB NES internal RAM equality after every authoritative boundary;
- controller-byte and frame-count equality;
- two representative 4-frame variable-input schedules;
- a sequential 8 x 4-frame shaped timing probe with native per-frame witnesses.

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
NES_INTERNAL_RAM_SIZE = 0x800

# Representative exact 4-frame schedules used for equivalence.
EQUIVALENCE_SCHEDULES: dict[str, tuple[int, ...]] = {
    "right_b_then_right_ab": (0x82, 0x83, 0x83, 0x83),
    "release_then_left_b": (0x00, 0x00, 0x42, 0x42),
}

# Cardinality/duration-shaped 8 x 4f workload for sequential timing. These are
# not claimed to be the full runtime candidate generator; the purpose here is
# to measure the native reset/schedule/witness floor before integrating policy.
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
            "FamiPixelSpecRunSchedule",
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

        self._run_schedule = dll.FamiPixelSpecRunSchedule
        self._run_schedule.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        ]
        self._run_schedule.restype = ctypes.c_int32

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
        """Run to PPU frame-count edges; diagnostic only, not exact PpuFrame semantics."""
        self._check("FamiPixelSpecRunFrames", self._run(int(count)))

    def run_schedule(
        self, schedule: tuple[int, ...], port: int = 0
    ) -> list[dict[str, int | bytes]]:
        if not schedule:
            raise ValueError("schedule must contain at least one frame")
        if not 0 <= port <= 1:
            raise ValueError("NES controller port must be 0 or 1")
        if any(not 0 <= int(buttons) <= 0xFF for buttons in schedule):
            raise ValueError("all NES controller states must fit in one byte")

        count = len(schedule)
        buttons_buf = (ctypes.c_uint8 * count)(*(int(v) for v in schedule))
        ram_buf = (ctypes.c_uint8 * (count * NES_INTERNAL_RAM_SIZE))()
        controller_buf = (ctypes.c_uint8 * count)()
        frame_count_buf = (ctypes.c_uint32 * count)()

        status = int(
            self._run_schedule(
                int(port),
                buttons_buf,
                int(count),
                ram_buf,
                int(len(ram_buf)),
                controller_buf,
                int(len(controller_buf)),
                frame_count_buf,
                int(len(frame_count_buf)),
            )
        )
        self._check("FamiPixelSpecRunSchedule", status)

        rows: list[dict[str, int | bytes]] = []
        for index in range(count):
            start = index * NES_INTERNAL_RAM_SIZE
            end = start + NES_INTERNAL_RAM_SIZE
            rows.append(
                {
                    "ram": bytes(ram_buf[start:end]),
                    "controller": int(controller_buf[index]),
                    "frame_count": int(frame_count_buf[index]),
                }
            )
        return rows

    def controller(self, port: int = 0) -> int:
        value = int(self._get_controller(int(port)))
        if value < 0:
            raise RuntimeError("FamiPixelSpecGetNesControllerState failed")
        return value

    def ram(self) -> bytes:
        buf = (ctypes.c_uint8 * NES_INTERNAL_RAM_SIZE)()
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

    live_rows: list[dict[str, int | bytes]] = []
    for buttons in schedule:
        set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(1, timeout_ms=timeout_ms)
        live_rows.append(
            {
                "ram": read_nes_internal_ram(core),
                "controller": int(get_nes_controller_state(core, 0)),
                "frame_count": int(core.frame_count()),
            }
        )

    # The speculative schedule stays continuous across all four exact PPU-frame
    # boundaries. This is required because a debugger PPU-frame boundary may be
    # inside a CPU instruction; returning/serializing there would lose call-stack
    # micro-position. The native listener captures the witness and resumes in the
    # same C++ call stack before the next period.
    spec_rows = spec.run_schedule(schedule, port=0)

    frames: list[dict] = []
    for index, (buttons, live, native) in enumerate(
        zip(schedule, live_rows, spec_rows), start=1
    ):
        live_ram = live["ram"]
        spec_ram = native["ram"]
        assert isinstance(live_ram, bytes)
        assert isinstance(spec_ram, bytes)
        ram_diff = _first_difference(live_ram, spec_ram)
        row = {
            "frame": index,
            "buttons": int(buttons),
            "live_frame_count": int(live["frame_count"]),
            "spec_frame_count": int(native["frame_count"]),
            "live_controller": int(live["controller"]),
            "spec_controller": int(native["controller"]),
            "ram_equal": ram_diff is None,
            "ram_first_difference": ram_diff,
        }
        frames.append(row)
        if (
            ram_diff is not None
            or int(live["controller"]) != int(native["controller"])
            or int(live["frame_count"]) != int(native["frame_count"])
        ):
            return {"name": name, "pass": False, "frames": frames}

    return {"name": name, "pass": True, "frames": frames}


def _benchmark_8x4(spec: SpecApi, samples: int) -> dict:
    totals: list[int] = []
    reset_totals: list[int] = []
    schedule_totals: list[int] = []

    for _ in range(samples):
        t_decision0 = time.perf_counter_ns()
        reset_ns = 0
        schedule_ns = 0
        for schedule in BENCH_SCHEDULES:
            t0 = time.perf_counter_ns()
            spec.reset_to_root()
            t1 = time.perf_counter_ns()
            reset_ns += t1 - t0

            t2 = time.perf_counter_ns()
            spec.run_schedule(schedule)
            t3 = time.perf_counter_ns()
            schedule_ns += t3 - t2

        t_decision1 = time.perf_counter_ns()
        totals.append(t_decision1 - t_decision0)
        reset_totals.append(reset_ns)
        schedule_totals.append(schedule_ns)

    return {
        "shape": (
            "8x4f sequential; root reset per candidate; one continuous exact "
            "PPU-period schedule call with 2KiB RAM/controller/frame witness per frame"
        ),
        "decision_total": _summary_ms(totals),
        "root_reset_total": _summary_ms(reset_totals),
        "native_schedule_witness_total": _summary_ms(schedule_totals),
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
            "schema": 2,
            "probe": "mesen-native-spec-runner-v0",
            "boundary_semantics": "Debugger::Step(StepType::PpuFrame) fixed PPU period",
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
            print(f"    first failing frame: {case['frames'][-1]}")

    bench = payload.get("benchmark")
    if bench is not None:
        print("\n8 x 4f sequential native exact-boundary benchmark")
        for key in (
            "decision_total",
            "root_reset_total",
            "native_schedule_witness_total",
        ):
            row = bench[key]
            print(
                f"  {key:30s} "
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
