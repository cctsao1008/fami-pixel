#!/usr/bin/env python3
"""Inspect SMB1 terrain validity and landing semantics from one scenario root.

This tool is for issue #31. It restores an extracted Mesen save-state scenario,
reads the current native SMB1 radar once, and prints the conservative terrain
validity evidence used by projected landing diagnostics.

It does not choose or execute a policy. Exact Mesen trajectory probes remain the
final authority for whether a candidate actually lands, dies, or completes.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import smb1_trajectory_probe as base

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import read_smb1_state
from fami_pixel.games.smb1.landing import assess_landing_zone
from fami_pixel.games.smb1.radar import read_smb1_radar


_DONE = "TerrainProbe: DONE"
_GRACE_S = 0.75


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Inspect SMB1 terrain SAFE/GAP/UNKNOWN semantics from a saved scenario"
    )
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-terrain-probe"))
    p.add_argument("--lookahead", type=int, default=192)
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    _manifest_path, manifest, state_file = base._load_manifest(scenario_dir)

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()

    try:
        base._restore(core, state_file, manifest)
        state = read_smb1_state(core)
        radar = read_smb1_radar(
            core,
            player_x=state.player_absolute_x,
            lookahead_px=args.lookahead,
        )
        radar_payload = radar.to_payload()
        landing = assess_landing_zone(radar_payload).to_payload()

        result = {
            "scenario": manifest.get("id"),
            "root_generation": manifest.get("root_generation"),
            "native_frame": core.frame_count(),
            "mario_x": state.player_absolute_x,
            "mario_y": state.player_y,
            "screen_right_x": radar.screen_right_x,
            "lookahead_px": radar.lookahead_px,
            "terrain_valid_through_x": radar.terrain_valid_through_x,
            "nearest_gap_dx": radar.nearest_gap_dx,
            "nearest_obstacle_dx": radar.nearest_obstacle_dx,
            "landing": landing,
            "columns": radar_payload["columns"],
        }

        if args.json_output:
            print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        else:
            print(f"Scenario   : {result['scenario']}", flush=True)
            print(
                f"Root       : generation={result['root_generation']} "
                f"frame={result['native_frame']} X={result['mario_x']} Y={result['mario_y']}",
                flush=True,
            )
            print(
                f"Coverage   : screen_right={radar.screen_right_x} "
                f"lookahead={radar.lookahead_px}px "
                f"valid_through={radar.terrain_valid_through_x}",
                flush=True,
            )
            print(
                f"Hazard     : gap={radar.nearest_gap_dx} obstacle={radar.nearest_obstacle_dx}",
                flush=True,
            )
            print(
                "Landing    : "
                f"terrain={landing['landing_terrain_status']} "
                f"status={landing['landing_status']} "
                f"safe={int(bool(landing['landing_safe']))} "
                f"enemy_count={landing['landing_enemy_count']} "
                f"samples={landing['landing_terrain_valid_sample_count']}/"
                f"{landing['landing_terrain_sample_count']}",
                flush=True,
            )
            for column in radar_payload["columns"]:
                address = column.get("surface_address")
                value = column.get("surface_value")
                address_text = "--" if address is None else f"0x{int(address):04X}"
                value_text = "--" if value is None else f"0x{int(value):02X}"
                print(
                    "Column     : "
                    f"x={int(column['x']):4d} dx={int(column['dx']):+4d} "
                    f"valid={column['validity']:7s} "
                    f"surface_row={str(column['surface_row']):>4s} "
                    f"addr={address_text} value={value_text}",
                    flush=True,
                )

        print(_DONE, flush=True)
        return 0
    finally:
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()


def supervise(args: argparse.Namespace) -> int:
    base._load_manifest(args.scenario_dir.expanduser().resolve())
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
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
                try:
                    return proc.wait(timeout=_GRACE_S)
                except subprocess.TimeoutExpired:
                    _terminate_tree(proc)
                    return 0
        return proc.wait()
    except KeyboardInterrupt:
        _terminate_tree(proc)
        return 130
    finally:
        if saw_done and proc.poll() is None:
            _terminate_tree(proc)


def main() -> int:
    args = parse_args()
    if args.lookahead <= 0:
        raise SystemExit("--lookahead must be > 0")
    return worker(args) if args.worker else supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
