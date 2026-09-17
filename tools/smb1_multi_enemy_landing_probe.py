#!/usr/bin/env python3
"""Machine gate for deterministic SMB1 multi-enemy landing acceptance.

The scenario must come from ``extract_smb1_multi_enemy_scenario.py`` and prove
that the selected live root had at least two enemies in the configured landing
corridor. The actual trajectory decision is delegated to the exact-Mesen generic
trajectory probe. PASS requires that probe to emit a resolved safe selection;
radar geometry alone is never accepted as trajectory proof.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


_DONE = "MultiGoombaLandingProbe: PASS"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the deterministic multi-Goomba landing acceptance gate")
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-multi-enemy-landing"))
    p.add_argument("--max-horizon", type=int, default=320)
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument("--landing-enemies-at-least", type=int, default=2)
    args = p.parse_args()
    if args.max_horizon <= 0:
        p.error("--max-horizon must be > 0")
    if args.step_timeout <= 0:
        p.error("--step-timeout must be > 0")
    if args.landing_enemies_at_least <= 0:
        p.error("--landing-enemies-at-least must be > 0")
    return args


def _load_manifest(scenario_dir: Path) -> dict:
    path = scenario_dir / "manifest.json"
    if not path.is_file():
        raise SystemExit(f"scenario manifest not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid scenario manifest: {path}: {exc}") from exc


def manifest_multi_enemy_count(manifest: dict) -> int:
    try:
        return max(0, int(manifest.get("selection_landing_enemy_count", 0)))
    except (TypeError, ValueError):
        return 0


def output_has_safe_selection(output: str) -> bool:
    """The generic probe prints SELECT only for Mesen-resolved safe results."""

    return any(line.startswith("SELECT     :") for line in output.splitlines())


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest = _load_manifest(scenario_dir)
    count = manifest_multi_enemy_count(manifest)
    required = int(args.landing_enemies_at_least)
    if count < required:
        raise SystemExit(
            "scenario is not a strict multi-enemy fixture: "
            f"selection_landing_enemy_count={count}, required>={required}. "
            "Re-extract with tools/extract_smb1_multi_enemy_scenario.py."
        )

    probe = Path(__file__).resolve().with_name("smb1_trajectory_probe.py")
    cmd = [
        sys.executable,
        str(probe),
        str(args.rom),
        str(scenario_dir),
        "--dll",
        str(args.dll),
        "--home",
        str(args.home),
        "--max-horizon",
        str(int(args.max_horizon)),
        "--step-timeout",
        str(float(args.step_timeout)),
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    output = completed.stdout or ""
    print(
        f"MultiEnemy : landing_enemy_count={count} required>={required} "
        f"selection_generation={manifest.get('selection_generation')}",
        flush=True,
    )
    print(output, end="" if output.endswith("\n") or not output else "\n", flush=True)

    if int(completed.returncode) != 0:
        raise SystemExit(f"trajectory probe failed with exit code {completed.returncode}")
    if not output_has_safe_selection(output):
        raise SystemExit(
            "multi-enemy deterministic gate failed: no Mesen-resolved safe trajectory was selected"
        )

    print(_DONE, flush=True)
    return 0


def main() -> int:
    return worker(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
