#!/usr/bin/env python3
"""Cross-process Mesen savestate gate for the V31/V32 shared COLLECT tree.

The optimization is valid only if a handoff state saved by one shadow Mesen
process can be restored by another process without changing the deterministic
future. This probe uses one extracted SMB1 scenario root and two isolated Mesen
processes:

producer:
    root -> fixed continuation -> save +8f/+12f branchpoints -> continue to +16f

consumer:
    restore +8f  -> compare full decoded SMB1 RAM state -> advance 4f -> compare +12f
    restore +12f -> compare full decoded SMB1 RAM state -> advance 4f -> compare +16f

A PASS therefore checks both immediate cross-process restore equality and one
four-frame future from each shared handoff. It does not run the live planner.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

_TOOLS = Path(__file__).resolve().parent
_EXAMPLES = _TOOLS.parent / "examples"
for path in (_TOOLS, _EXAMPLES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import smb1_trajectory_probe as probe
import mesen_smb_checkpoint_planner as base

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import read_smb1_state


_HANDOFFS = (8, 12)
_FINAL_AGE = 16
_DONE = "SharedCollectStateProbe: DONE"
_PASS = "SharedCollectStateProbe: PASS"
_GRACE_S = 0.75


def _int_auto(value: str) -> int:
    return int(value, 0)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify cross-process Mesen handoff savestates for shared COLLECT search"
    )
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-shared-collect-probe"))
    p.add_argument("--output", type=Path, default=Path("build/shared-collect-state-probe"))
    p.add_argument("--buttons", type=_int_auto, default=0x82, help="NES byte; default RIGHT+B (0x82)")
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument("--role", choices=("producer", "consumer"), help=argparse.SUPPRESS)
    return p.parse_args()


def _signature(core) -> dict:
    state = read_smb1_state(core)
    payload = asdict(state)
    payload["native_frame"] = int(core.frame_count())
    payload["player_absolute_x"] = int(state.player_absolute_x)
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _load_core(args: argparse.Namespace, home_suffix: str):
    core = MesenCore(args.dll)
    home = Path(f"{args.home.expanduser().resolve()}-{home_suffix}")
    core.initialize_headless(home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()
    return core


def _step_fixed(core, buttons: int, timeout_s: float) -> None:
    base.set_nes_controller_state(core, 0, int(buttons))
    base.step(core, timeout_s)


def producer(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    _manifest_path, scenario, root_state = probe._load_manifest(scenario_dir)
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"

    core = _load_core(args, "producer")
    try:
        probe._restore(core, root_state, scenario)
        root_frame = int(core.frame_count())
        signatures = {"0": _signature(core)}
        states: dict[str, dict] = {}

        for age in range(1, _FINAL_AGE + 1):
            _step_fixed(core, args.buttons, args.step_timeout)
            if age in _HANDOFFS:
                state_path = output / f"handoff-{age:02d}.mss"
                frame, x, engine = base.save_checkpoint(core, state_path)
                signatures[str(age)] = _signature(core)
                states[str(age)] = {
                    "checkpoint": str(state_path),
                    "frame": int(frame),
                    "x": int(x),
                    "engine": int(engine),
                }
            elif age == _FINAL_AGE:
                signatures[str(age)] = _signature(core)

        payload = {
            "schema": 1,
            "scenario_id": scenario.get("id"),
            "root_frame": root_frame,
            "buttons": int(args.buttons),
            "handoffs": list(_HANDOFFS),
            "final_age": _FINAL_AGE,
            "states": states,
            "signatures": signatures,
        }
        _write_json(manifest_path, payload)
        print(
            f"Producer   : root={root_frame} handoffs={_HANDOFFS} "
            f"buttons=0x{int(args.buttons):02X}",
            flush=True,
        )
        print(f"Manifest   : {manifest_path}", flush=True)
        return 0
    finally:
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _diff(expected: dict, actual: dict) -> list[str]:
    keys = sorted(set(expected) | set(actual))
    return [
        f"{key}: expected={expected.get(key)!r} actual={actual.get(key)!r}"
        for key in keys
        if expected.get(key) != actual.get(key)
    ]


def consumer(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    buttons = int(manifest["buttons"])

    core = _load_core(args, "consumer")
    failures: list[str] = []
    try:
        for handoff in _HANDOFFS:
            point = manifest["states"][str(handoff)]
            base.restore_checkpoint(
                core,
                Path(point["checkpoint"]),
                int(point["frame"]),
                int(point["x"]),
                int(point["engine"]),
            )
            expected = manifest["signatures"][str(handoff)]
            actual = _signature(core)
            immediate_diff = _diff(expected, actual)
            if immediate_diff:
                failures.extend(f"restore@{handoff}: {item}" for item in immediate_diff)
                continue

            target_age = handoff + 4
            for _ in range(4):
                _step_fixed(core, buttons, args.step_timeout)
            expected_future = manifest["signatures"][str(target_age)]
            actual_future = _signature(core)
            future_diff = _diff(expected_future, actual_future)
            if future_diff:
                failures.extend(
                    f"future@{handoff}->{target_age}: {item}" for item in future_diff
                )
                continue

            print(
                f"Handoff {handoff:02d}: PASS restore + deterministic 4f future -> age {target_age}",
                flush=True,
            )

        if failures:
            print("SharedCollectStateProbe: FAIL", flush=True)
            for item in failures[:40]:
                print(f"  {item}", flush=True)
            return 5
        print(_PASS, flush=True)
        print(_DONE, flush=True)
        return 0
    finally:
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _run_role(args: argparse.Namespace, role: str) -> int:
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        str(args.rom),
        str(args.scenario_dir),
        "--dll",
        str(args.dll),
        "--home",
        str(args.home),
        "--output",
        str(args.output),
        "--buttons",
        hex(int(args.buttons)),
        "--step-timeout",
        str(float(args.step_timeout)),
        "--role",
        role,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    saw_done = False
    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            if _DONE in line:
                saw_done = True
        return proc.wait()
    finally:
        if saw_done and proc.poll() is None:
            try:
                proc.wait(timeout=_GRACE_S)
            except subprocess.TimeoutExpired:
                probe._terminate_tree(proc)


def supervise(args: argparse.Namespace) -> int:
    probe._load_manifest(args.scenario_dir.expanduser().resolve())
    producer_code = _run_role(args, "producer")
    if producer_code != 0:
        return int(producer_code)
    return int(_run_role(args, "consumer"))


def main() -> int:
    args = parse_args()
    if args.role == "producer":
        return producer(args)
    if args.role == "consumer":
        return consumer(args)
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
