#!/usr/bin/env python3
"""Deterministic #32 probe for surrogate rank/prune -> exact Mesen search.

This tool exercises the same ordinary-PROGRESS search contract introduced by
V27 from one extracted local scenario root.  It is deliberately a narrow gate
before another full live run:

    bounded multi-chunk generation
        -> TinySurrogateMLP rank/prune
        -> selected top-K exact Mesen branches
        -> safe resolved outcome selection

The surrogate only spends the emulator budget; it never certifies safety.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import smb1_trajectory_probe as probe

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import (
    evaluate_mesen_trajectory,
    observation_from_state,
    read_smb1_state,
    trajectory_outcome_key,
)
from fami_pixel.games.smb1.forward_model import select_safe_resolved_result
from fami_pixel.games.smb1.trajectory_search import (
    DEFAULT_SEARCH_DEPTH,
    DEFAULT_TOP_K,
    build_search_frontier,
)
from fami_pixel.learning.tiny_mlp import TinySurrogateMLP


_DONE = "BoundedSearchProbe: DONE"
_GRACE_S = 0.75


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probe bounded multi-chunk surrogate rank/prune with exact Mesen"
    )
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("surrogate_model", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-bounded-search-probe"))
    p.add_argument("--depth", type=int, default=DEFAULT_SEARCH_DEPTH)
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p.add_argument("--max-horizon", type=int, default=320)
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    _manifest_path, manifest, state_file = probe._load_manifest(scenario_dir)
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    if args.depth <= 0 or args.top_k <= 0 or args.max_horizon <= 0:
        raise SystemExit("depth, top-k, and max-horizon must be > 0")

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()

    print(f"Scenario   : {manifest.get('id')}", flush=True)
    print(
        f"Root       : generation={manifest.get('root_generation')} "
        f"frame={manifest.get('native_frame')} X={manifest.get('mario_x')} "
        f"Y={manifest.get('mario_y')}",
        flush=True,
    )

    results = []
    try:
        probe._restore(core, state_file, manifest)
        observation = observation_from_state(core.frame_count(), read_smb1_state(core))
        model = TinySurrogateMLP.load_json(model_path)
        frontier = build_search_frontier(
            observation,
            model,
            depth=args.depth,
            top_k=args.top_k,
        )
        print(
            f"Search     : depth={frontier.depth} generated={frontier.generated} "
            f"pruned={frontier.pruned} top_k={len(frontier.ranked)}",
            flush=True,
        )
        for index, ranked in enumerate(frontier.ranked):
            print(
                f"RANK {index:02d}   : {ranked.plan.name:42s} "
                f"dx={ranked.predicted_delta_x:+7.2f} "
                f"risk={ranked.risk_probability:.3f} "
                f"stall={ranked.no_progress_probability:.3f} "
                f"anchor={int(ranked.anchored)}",
                flush=True,
            )

        print("Exact Mesen:", flush=True)
        for ranked in frontier.ranked:
            probe._restore(core, state_file, manifest)
            start = observation_from_state(core.frame_count(), read_smb1_state(core))
            result = evaluate_mesen_trajectory(
                core,
                ranked.plan,
                max_horizon_frames=args.max_horizon,
                step_timeout_s=args.step_timeout,
                start_observation=start,
                stop_on_landing=True,
            )
            results.append(result)
            status = "UNRESOLVED" if result.event.value == "horizon" else "RESOLVED"
            print(
                f"{ranked.plan.name:42s} event={result.event.value:19s} "
                f"status={status:10s} frames={result.frames_simulated:3d} "
                f"dx={result.progress:+4d} maxdx={result.max_progress:+4d} "
                f"y={result.start_y:3d}->{result.end_y:3d}",
                flush=True,
            )

        selected = select_safe_resolved_result(results)
        if selected is None:
            print("NO SAFE    : top-K contains no Mesen-resolved non-death trajectory", flush=True)
        else:
            print(
                f"SELECT     : {selected.plan.name} -> {selected.event.value} "
                f"key={trajectory_outcome_key(selected)}",
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


def supervise(args: argparse.Namespace) -> int:
    probe._load_manifest(args.scenario_dir.expanduser().resolve())
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
                    probe._terminate_tree(proc)
                    return 0
        return proc.wait()
    except KeyboardInterrupt:
        probe._terminate_tree(proc)
        return 130
    finally:
        if saw_done and proc.poll() is None:
            probe._terminate_tree(proc)


def main() -> int:
    args = parse_args()
    return worker(args) if args.worker else supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
