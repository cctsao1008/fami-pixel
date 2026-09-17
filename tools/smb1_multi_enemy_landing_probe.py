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

Before the acceptance replay, a short exact-Mesen controller diagnostic restores
the same root and compares 8-frame RIGHT+B and LEFT+B branches. The diagnostic is
run in a dedicated child process because MesenCore's native InitDll/Release
lifecycle is process-global and is not safely reinitialized in the same Python
process. This is not an acceptance shortcut: it only distinguishes a
non-player-control / non-responsive root from a real trajectory-policy failure.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


_DONE = "MultiGoombaLandingProbe: PASS"
_DIAG_MARKER = "RootControlDiagnostic: "


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the deterministic multi-Goomba landing acceptance gate")
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-multi-enemy-landing"))
    p.add_argument("--max-horizon", type=int, default=320)
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument("--landing-enemies-at-least", type=int, default=2)
    p.add_argument("--diag-worker", action="store_true", help=argparse.SUPPRESS)
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


def _control_signature(observation) -> tuple[int, int, int, int, int, int]:
    return (
        int(observation.mario_x_abs),
        int(observation.mario_y),
        int(observation.game_engine_subroutine),
        int(observation.player_state),
        int(observation.player_x_speed),
        int(observation.player_y_speed),
    )


def _diagnose_root_control(generic, args: argparse.Namespace, scenario_dir: Path, manifest: dict) -> dict:
    """Compare short opposite-input branches from the exact fixture root."""

    state_file = scenario_dir / str(manifest.get("state_file", "root.mss"))
    diag_home = Path(f"{args.home}-diag")
    core = generic.MesenCore(args.dll)
    core.initialize_headless(diag_home)
    generic.configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL during root-control diagnostic")
    core.initialize_debugger()

    branches = {}
    try:
        generic._restore(core, state_file, manifest)
        root = generic.observation_from_state(core.frame_count(), generic.read_smb1_state(core))
        print(
            "RootState  : "
            f"engine=0x{int(root.game_engine_subroutine):02X} "
            f"oper={int(root.oper_mode)}/{int(root.oper_mode_task)} "
            f"pstate={int(root.player_state)} yhigh={int(root.mario_y_high)} "
            f"vx={int(root.player_x_speed)} vy={int(root.player_y_speed)} "
            f"joy=0x{int(root.raw_joypad):02X} control={int(bool(root.is_player_control))}",
            flush=True,
        )

        for name, action in (
            ("right8", generic.Smb1Action.RIGHT_B),
            ("left8", generic.Smb1Action.LEFT_B),
        ):
            generic._restore(core, state_file, manifest)
            start = generic.observation_from_state(core.frame_count(), generic.read_smb1_state(core))
            plan = generic.TrajectoryPlan(
                f"diag_{name}",
                (generic.ActionCommand(action, 8),),
                tail_action=action,
            )
            result = generic.evaluate_mesen_trajectory(
                core,
                plan,
                max_horizon_frames=8,
                step_timeout_s=float(args.step_timeout),
                target_reward_type=None,
                start_observation=start,
                stop_on_landing=False,
            )
            end = generic.observation_from_state(core.frame_count(), generic.read_smb1_state(core))
            signature = _control_signature(end)
            branches[name] = signature
            print(
                f"Control8   : {name:6s} event={result.event.value:7s} "
                f"X={int(end.mario_x_abs)} Y={int(end.mario_y)} "
                f"engine=0x{int(end.game_engine_subroutine):02X} "
                f"pstate={int(end.player_state)} vx={int(end.player_x_speed)} "
                f"vy={int(end.player_y_speed)} joy=0x{int(end.raw_joypad):02X}",
                flush=True,
            )

        responsive = branches.get("right8") != branches.get("left8")
        print(
            "Control8   : "
            f"responsive={int(bool(responsive))} "
            f"root_player_control={int(bool(root.is_player_control))}",
            flush=True,
        )
        return {
            "root_player_control": bool(root.is_player_control),
            "responsive": bool(responsive),
            "root_engine": int(root.game_engine_subroutine),
            "right8": branches.get("right8"),
            "left8": branches.get("left8"),
        }
    finally:
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _diagnostic_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        str(args.rom),
        str(args.scenario_dir),
        "--dll",
        str(args.dll),
        "--home",
        str(args.home),
        "--step-timeout",
        str(float(args.step_timeout)),
        "--diag-worker",
    ]


def _parse_diagnostic_output(output: str) -> dict:
    payloads = [
        line[len(_DIAG_MARKER) :]
        for line in output.splitlines()
        if line.startswith(_DIAG_MARKER)
    ]
    if len(payloads) != 1:
        raise RuntimeError(
            "root-control diagnostic did not emit exactly one machine payload"
        )
    try:
        value = json.loads(payloads[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid root-control diagnostic payload") from exc
    if not isinstance(value, dict):
        raise RuntimeError("root-control diagnostic payload is not an object")
    return value


def _diagnose_root_control_subprocess(args: argparse.Namespace) -> dict:
    """Run native Mesen root diagnostics in a disposable process.

    MesenCore Release() tears down process-global native state; calling InitDll()
    again in the same Python process can access-violate. Keep the acceptance
    worker's one InitDll lifecycle pristine by isolating this preflight.
    """

    completed = subprocess.run(
        _diagnostic_command(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    output = completed.stdout or ""
    print(output, end="" if output.endswith("\n") or not output else "\n", flush=True)
    if int(completed.returncode) != 0:
        raise SystemExit(
            f"root-control diagnostic failed with exit code {completed.returncode}"
        )
    return _parse_diagnostic_output(output)


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

    diagnostic = _diagnose_root_control_subprocess(args)
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

    if not diagnostic["root_player_control"]:
        raise SystemExit(
            "multi-enemy deterministic gate failed: fixture root is not in SMB1 "
            f"player-control subroutine (engine=0x{int(diagnostic['root_engine']):02X}); "
            "the semantic selector must choose a controller-authoritative root"
        )
    if not diagnostic["responsive"]:
        raise SystemExit(
            "multi-enemy deterministic gate failed: exact root produced identical "
            "8-frame RIGHT+B and LEFT+B control signatures; inspect checkpoint/root "
            "authority before changing the trajectory policy"
        )
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


def _diag_worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest = _load_manifest(scenario_dir)
    generic = _load_generic_probe()
    diagnostic = _diagnose_root_control(generic, args, scenario_dir, manifest)
    print(_DIAG_MARKER + json.dumps(diagnostic, separators=(",", ":")), flush=True)
    return 0


def main() -> int:
    args = parse_args()
    return _diag_worker(args) if args.diag_worker else worker(args)


if __name__ == "__main__":
    raise SystemExit(main())
