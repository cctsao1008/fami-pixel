#!/usr/bin/env python3
"""A/B probe for Mesen wall-clock pacing under exact debugger stepping.

Issue #40 found that FamiPixelStepFrame() advances at ~60 fps even on a fast host.
This probe keeps the same ROM/state/core and compares normal pacing against
Mesen's verified EmulationFlags::MaximumSpeed (0x04) through the existing
SetEmulationFlag export. It is intentionally a probe: the production runtime is
not changed by this file.

The probe compares scalar and batched stepping, including a V35-shaped 8 x 4f
slot-restored branch rollout. It also replays one scripted 32-frame input sequence
in both pacing modes and checks that decoded SMB1 RAM state is identical.
MaximumSpeed is interesting only if it changes wall-clock pacing without changing
the deterministic emulated result.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import ctypes
import json
from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import mesen_boundary_benchmark as bench

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import read_smb1_state


EMULATION_FLAG_MAXIMUM_SPEED = 0x04
DETERMINISM_FRAMES = 32
NES_A = 0x01
NES_B = 0x02
NES_RIGHT = 0x80
SCRIPTED_INPUT = (
    (NES_RIGHT,) * 8
    + (NES_RIGHT | NES_A,) * 8
    + (NES_RIGHT | NES_B,) * 8
    + (0x00,) * 8
)


def _int_auto(value: str) -> int:
    return int(value, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare exact Mesen stepping with and without MaximumSpeed pacing"
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home-max-speed-probe"))
    parser.add_argument("--save-state", type=Path)
    parser.add_argument("--output", type=Path, default=Path("build/mesen-max-speed-probe.json"))
    parser.add_argument("--buttons", type=_int_auto, default=0x00)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--batch-frames", type=int, default=4)
    parser.add_argument("--step-samples", type=int, default=120)
    parser.add_argument("--branch-samples", type=int, default=6)
    parser.add_argument("--warmup", type=int, default=4)
    parser.add_argument("--slot", type=int, default=9)
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if float(args.step_timeout) <= 0:
        raise SystemExit("--step-timeout must be > 0")
    if int(args.batch_frames) <= 1:
        raise SystemExit("--batch-frames must be > 1")
    if int(args.step_samples) <= 0:
        raise SystemExit("--step-samples must be > 0")
    if int(args.branch_samples) <= 0:
        raise SystemExit("--branch-samples must be > 0")
    if int(args.warmup) < 0:
        raise SystemExit("--warmup must be >= 0")
    if int(args.slot) < 0:
        raise SystemExit("--slot must be >= 0")


def _set_maximum_speed(core: MesenCore, enabled: bool) -> None:
    """Bind the source-verified stock Mesen SetEmulationFlag ABI locally.

    In pinned MesenCE, SetEmulationFlag(EmulationFlags, bool) is exported by
    ConfigApiWrapper.cpp and EmulationFlags::MaximumSpeed == 0x04. Keeping this
    binding local to the probe prevents a benchmark hypothesis from silently
    becoming a production runtime dependency before native validation.
    """

    if not core.has_export("SetEmulationFlag"):
        raise RuntimeError("MesenCore.dll does not export SetEmulationFlag")
    setter = core._dll.SetEmulationFlag  # probe-only access to verified native ABI
    setter.argtypes = [ctypes.c_int, ctypes.c_bool]
    setter.restype = None
    setter(int(EMULATION_FLAG_MAXIMUM_SPEED), bool(enabled))


def _restore(core: MesenCore, slot: int) -> None:
    core.load_state_slot(int(slot))


def _step_sequence(core: MesenCore, *, buttons_seq: tuple[int, ...], timeout_ms: int) -> None:
    for buttons in buttons_seq:
        set_nes_controller_state(core, 0, int(buttons))
        core.step_frame_sync(1, int(timeout_ms))


def _state_signature(core: MesenCore) -> dict:
    return asdict(read_smb1_state(core))


def _measure_mode(
    core: MesenCore,
    args: argparse.Namespace,
    *,
    maximum_speed: bool,
    timeout_ms: int,
) -> dict[str, dict]:
    _set_maximum_speed(core, maximum_speed)
    results: dict[str, dict] = {}

    _restore(core, args.slot)
    set_nes_controller_state(core, 0, int(args.buttons))
    results["step_sync_1"] = bench.measure(
        lambda: core.step_frame_sync(1, timeout_ms),
        samples=int(args.step_samples),
        warmup=int(args.warmup),
        units_per_sample=1,
        unit="frame",
    )

    _restore(core, args.slot)
    set_nes_controller_state(core, 0, int(args.buttons))
    batch = int(args.batch_frames)
    results["step_sync_batch"] = bench.measure(
        lambda: core.step_frame_sync(batch, timeout_ms),
        samples=max(1, int(args.step_samples) // batch),
        warmup=max(1, int(args.warmup) // batch),
        units_per_sample=batch,
        unit="frame",
    )
    results["step_sync_batch"]["batch_frames"] = batch

    _restore(core, args.slot)
    results["branch_8x4_slot_scalar"] = bench.measure(
        lambda: bench._branch_scalar(
            core,
            restore=lambda: _restore(core, args.slot),
            buttons=int(args.buttons),
            timeout_ms=timeout_ms,
        ),
        samples=int(args.branch_samples),
        warmup=min(2, int(args.warmup)),
        units_per_sample=1,
        unit="decision",
    )
    results["branch_8x4_slot_scalar"]["simulated_frames_per_decision"] = (
        bench.BRANCH_COUNT * bench.BRANCH_FRAMES
    )

    _restore(core, args.slot)
    results["branch_8x4_slot_batch"] = bench.measure(
        lambda: bench._branch_batch(
            core,
            restore=lambda: _restore(core, args.slot),
            buttons=int(args.buttons),
            timeout_ms=timeout_ms,
        ),
        samples=int(args.branch_samples),
        warmup=min(2, int(args.warmup)),
        units_per_sample=1,
        unit="decision",
    )
    results["branch_8x4_slot_batch"]["simulated_frames_per_decision"] = (
        bench.BRANCH_COUNT * bench.BRANCH_FRAMES
    )

    return results


def _speedups(normal: dict[str, dict], maximum: dict[str, dict]) -> dict[str, float]:
    values: dict[str, float] = {}
    for name in sorted(set(normal) & set(maximum)):
        normal_p50 = float(normal[name]["p50_us_per_unit"])
        maximum_p50 = float(maximum[name]["p50_us_per_unit"])
        if maximum_p50 > 0:
            values[name] = normal_p50 / maximum_p50
    return values


def _within_mode_batch_gain(results: dict[str, dict]) -> dict[str, float]:
    values: dict[str, float] = {}
    scalar = results.get("step_sync_1")
    batch = results.get("step_sync_batch")
    if scalar and batch and float(batch["p50_us_per_unit"]) > 0:
        values["step_batch_speedup_per_frame"] = (
            float(scalar["p50_us_per_unit"]) / float(batch["p50_us_per_unit"])
        )
    branch_scalar = results.get("branch_8x4_slot_scalar")
    branch_batch = results.get("branch_8x4_slot_batch")
    if branch_scalar and branch_batch and float(branch_batch["p50_us_per_unit"]) > 0:
        values["branch_batch_speedup"] = (
            float(branch_scalar["p50_us_per_unit"])
            / float(branch_batch["p50_us_per_unit"])
        )
    return values


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

        normal = _measure_mode(
            core,
            args,
            maximum_speed=False,
            timeout_ms=timeout_ms,
        )
        maximum = _measure_mode(
            core,
            args,
            maximum_speed=True,
            timeout_ms=timeout_ms,
        )

        # Determinism witness: replay the exact same non-trivial 32 input frames
        # from the same save slot under both pacing modes and compare decoded RAM.
        _restore(core, args.slot)
        _set_maximum_speed(core, False)
        _step_sequence(core, buttons_seq=SCRIPTED_INPUT, timeout_ms=timeout_ms)
        normal_signature = _state_signature(core)

        _restore(core, args.slot)
        _set_maximum_speed(core, True)
        _step_sequence(core, buttons_seq=SCRIPTED_INPUT, timeout_ms=timeout_ms)
        maximum_signature = _state_signature(core)
        deterministic_match = normal_signature == maximum_signature

        payload = {
            "schema": 2,
            "probe": "mesen-maximum-speed",
            "mesen": {
                "dll": str(Path(args.dll).expanduser().resolve()),
                "version": int(core.version()),
                "build_date": core.build_date(),
                "maximum_speed_flag": EMULATION_FLAG_MAXIMUM_SPEED,
            },
            "input": {
                "rom": str(Path(args.rom).expanduser().resolve()),
                "save_state": None
                if args.save_state is None
                else str(Path(args.save_state).expanduser().resolve()),
                "buttons": int(args.buttons),
                "step_timeout_s": float(args.step_timeout),
                "batch_frames": int(args.batch_frames),
                "determinism_frames": DETERMINISM_FRAMES,
                "determinism_script": [int(value) for value in SCRIPTED_INPUT],
            },
            "normal": normal,
            "maximum_speed": maximum,
            "speedup_p50": _speedups(normal, maximum),
            "within_mode_batch_gain": {
                "normal": _within_mode_batch_gain(normal),
                "maximum_speed": _within_mode_batch_gain(maximum),
            },
            "determinism": {
                "decoded_smb1_state_match": bool(deterministic_match),
            },
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


def _print_mode(name: str, results: dict[str, dict]) -> None:
    print(name, flush=True)
    print("phase                              p50 us/unit p95 us/unit p99 us/unit   throughput", flush=True)
    for phase, row in results.items():
        print(bench._format_row(phase, row), flush=True)


def print_report(payload: dict, output: Path) -> None:
    print("Mesen MaximumSpeed A/B probe", flush=True)
    _print_mode("\nnormal pacing", payload["normal"])
    _print_mode("\nmaximum speed", payload["maximum_speed"])
    print("\np50 speedups normal -> maximum", flush=True)
    for name, value in payload["speedup_p50"].items():
        print(f"  {name}: {float(value):.3f}x", flush=True)
    print("\nwithin-mode batching gains", flush=True)
    for mode, gains in payload["within_mode_batch_gain"].items():
        print(f"  {mode}", flush=True)
        for name, value in gains.items():
            print(f"    {name}: {float(value):.3f}x", flush=True)
    match = bool(payload["determinism"]["decoded_smb1_state_match"])
    print(f"\ndeterminism scripted 32f decoded SMB1 state: {'PASS' if match else 'FAIL'}", flush=True)
    print(f"JSON: {output.expanduser().resolve()}", flush=True)


def main() -> int:
    args = parse_args()
    payload = run(args)
    print_report(payload, args.output)
    return 0 if payload["determinism"]["decoded_smb1_state_match"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
