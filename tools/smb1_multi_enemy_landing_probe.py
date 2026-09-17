#!/usr/bin/env python3
"""Machine gate for deterministic SMB1 multi-enemy landing acceptance.

The scenario must come from ``extract_smb1_multi_enemy_scenario.py`` and prove
that the selected live root had at least two enemies in the configured landing
corridor, that the nearest cluster itself is a projected multi-enemy cluster
inside that corridor, and that the live planner actually selected a landing-zone
guard.

The generic exact-Mesen trajectory probe is reused, but this wrapper augments its
candidate set with the exact V20/V26 live landing actions:

- airborne ``landing-zone-extend`` -> RIGHT+A+B for 8f, then RIGHT+B;
- grounded ``landing-zone-escape`` -> RIGHT+B 1f, RIGHT+A+B 15f, then RIGHT+B.

PASS requires the live guard action itself to reach a Mesen-resolved safe landing
(or win), and the generic probe must also report a safe resolved selection. Radar
geometry alone is never accepted as trajectory proof.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace


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


def _load_generic_probe():
    path = Path(__file__).resolve().with_name("smb1_trajectory_probe.py")
    spec = spec_from_file_location("smb1_trajectory_probe_multi_enemy_gate", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def manifest_multi_enemy_count(manifest: dict) -> int:
    try:
        return max(0, int(manifest.get("selection_landing_enemy_count", 0)))
    except (TypeError, ValueError):
        return 0


def manifest_landing_guard(manifest: dict) -> str:
    value = manifest.get("selection_guard_mode")
    return "" if value is None else str(value)


def manifest_projected_cluster(manifest: dict) -> tuple[int, int | None, int | None, int, int]:
    def _int(name: str, default: int | None = None) -> int | None:
        value = manifest.get(name)
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    count = max(0, int(_int("selection_nearest_cluster_count", 0) or 0))
    start = _int("selection_nearest_cluster_start_dx")
    end = _int("selection_nearest_cluster_end_dx")
    near = int(_int("selection_landing_corridor_start_dx", 96) or 96)
    far = int(_int("selection_landing_corridor_end_dx", 160) or 160)
    return count, start, end, near, far


def manifest_has_clean_projected_cluster(manifest: dict, required: int) -> bool:
    count, start, end, near, far = manifest_projected_cluster(manifest)
    if count < int(required) or start is None or end is None:
        return False
    return near <= start <= end <= far


def expected_live_plan_name(guard: str) -> str | None:
    if str(guard).startswith("landing-zone-extend"):
        return "live_cluster_extend_8"
    if str(guard).startswith("landing-zone-escape"):
        return "live_cluster_jump_16"
    return None


def output_has_safe_selection(output: str) -> bool:
    """The generic probe prints SELECT only for Mesen-resolved safe results."""

    return any(line.startswith("SELECT     :") for line in output.splitlines())


def output_has_live_guard_success(output: str, guard: str) -> bool:
    """Require the exact live landing action to resolve as LANDED or WIN."""

    expected = expected_live_plan_name(guard)
    if expected is None:
        return False
    for line in output.splitlines():
        if not line.startswith(expected):
            continue
        if "event=landed" in line or "event=win" in line:
            return True
    return False


def _live_landing_plans(generic) -> tuple:
    return (
        generic.TrajectoryPlan(
            "live_cluster_extend_8",
            (generic.ActionCommand(generic.Smb1Action.RIGHT_A_B, 8),),
            tail_action=generic.Smb1Action.RIGHT_B,
        ),
        generic.TrajectoryPlan(
            "live_cluster_jump_16",
            (
                generic.ActionCommand(generic.Smb1Action.RIGHT_B, 1),
                generic.ActionCommand(generic.Smb1Action.RIGHT_A_B, 15),
            ),
            tail_action=generic.Smb1Action.RIGHT_B,
        ),
    )


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest = _load_manifest(scenario_dir)
    count = manifest_multi_enemy_count(manifest)
    required = int(args.landing_enemies_at_least)
    guard = manifest_landing_guard(manifest)
    expected_plan = expected_live_plan_name(guard)
    cluster_count, cluster_start, cluster_end, corridor_start, corridor_end = (
        manifest_projected_cluster(manifest)
    )

    if count < required:
        raise SystemExit(
            "scenario is not a strict multi-enemy fixture: "
            f"selection_landing_enemy_count={count}, required>={required}. "
            "Re-extract with tools/extract_smb1_multi_enemy_scenario.py."
        )
    if not manifest_has_clean_projected_cluster(manifest, required):
        raise SystemExit(
            "scenario is not a clean projected multi-enemy fixture: "
            f"cluster={cluster_count}@{cluster_start}..{cluster_end}, "
            f"corridor={corridor_start}..{corridor_end}, required>={required}. "
            "Re-extract with the current tools/extract_smb1_multi_enemy_scenario.py."
        )
    if expected_plan is None:
        raise SystemExit(
            "scenario is not an active landing-policy fixture: "
            f"selection_guard_mode={guard!r}. Re-extract with the current "
            "tools/extract_smb1_multi_enemy_scenario.py."
        )

    generic = _load_generic_probe()
    original_plans = generic.PLANS
    generic.PLANS = _live_landing_plans(generic) + tuple(original_plans)
    probe_args = SimpleNamespace(
        rom=args.rom,
        scenario_dir=scenario_dir,
        dll=args.dll,
        home=args.home,
        max_horizon=int(args.max_horizon),
        step_timeout=float(args.step_timeout),
        target_reward=None,
    )

    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            return_code = int(generic.worker(probe_args))
    finally:
        generic.PLANS = original_plans
    output = capture.getvalue()

    print(
        f"MultiEnemy : landing_enemy_count={count} required>={required} "
        f"selection_generation={manifest.get('selection_generation')} "
        f"cluster={cluster_count}@{cluster_start}..{cluster_end} "
        f"corridor={corridor_start}..{corridor_end} "
        f"guard={guard} expected_plan={expected_plan}",
        flush=True,
    )
    print(output, end="" if output.endswith("\n") or not output else "\n", flush=True)

    if return_code != 0:
        raise SystemExit(f"trajectory probe failed with exit code {return_code}")
    if not output_has_live_guard_success(output, guard):
        raise SystemExit(
            "multi-enemy deterministic gate failed: the exact live landing action "
            f"{expected_plan} did not reach a Mesen-resolved landing/win"
        )
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
