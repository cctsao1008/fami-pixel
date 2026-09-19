#!/usr/bin/env python3
"""Compare V35 live-core and V36 native-spec Star MPC at one exact scenario root.

The tool restores one fami-pixel reward-visible Mesen scenario, computes the V35
current-root decision, restores the same root again, and computes V36 through the
separate native speculative emulator.  PASS requires the complete policy result
and every candidate's semantic evidence/ranking to match; timing fields are
reported but excluded from equality.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import observation_from_state, read_smb1_state
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.games.smb1.reward_live import StickyCollectObjective
from fami_pixel.games.smb1.reward_target import read_active_reward_target


_EXAMPLES = (Path(__file__).resolve().parents[1] / "examples").resolve()
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

import mesen_smb_checkpoint_planner_v35 as v35  # noqa: E402
import mesen_smb_checkpoint_planner_v36 as v36  # noqa: E402


_DONE = "V36NativeStarCompare: PASS"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare V35 and V36 exact Star MPC from one fami-pixel Mesen scenario root"
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("scenario_dir", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home-v36-star-compare"))
    parser.add_argument("--step-timeout", type=float, default=5.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/v36-native-star-compare.json"),
    )
    args = parser.parse_args()
    if args.step_timeout <= 0:
        parser.error("--step-timeout must be > 0")
    return args


def _load_manifest(scenario_dir: Path) -> tuple[dict, Path]:
    manifest_path = scenario_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"scenario manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state_file = scenario_dir / str(manifest.get("state_file", "root.mss"))
    if not state_file.is_file():
        raise SystemExit(f"scenario state not found: {state_file}")
    return manifest, state_file


def _restore_root(core: MesenCore, state_file: Path, manifest: dict) -> None:
    expected_frame = int(manifest["native_frame"])
    expected_x = int(manifest["mario_x"])
    core.load_state_file(state_file)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        state = read_smb1_state(core)
        if core.frame_count() == expected_frame and state.player_absolute_x == expected_x:
            return
        time.sleep(0.002)
    state = read_smb1_state(core)
    raise RuntimeError(
        "scenario root restore did not converge: "
        f"expected frame={expected_frame} x={expected_x}, "
        f"actual frame={core.frame_count()} x={state.player_absolute_x}"
    )


def _collect_radar(core: MesenCore, objective: StickyCollectObjective) -> tuple[object, dict]:
    current = observation_from_state(core.frame_count(), read_smb1_state(core))
    payload = read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
    try:
        tracked = read_active_reward_target(core, player_x=current.mario_x_abs)
    except Exception:
        tracked = None
    snapshot = objective.update(
        frame=int(core.frame_count()),
        radar=payload,
        tracked_reward=tracked,
    )
    payload["objective_mode"] = snapshot["mode"]
    payload["collect_target_type"] = snapshot["target_type"]
    payload["collect_target"] = snapshot["target"]
    payload["collect_target_sticky"] = bool(snapshot.get("sticky", False))
    if snapshot.get("collected_type") is not None:
        payload["reward_collected_type"] = snapshot["collected_type"]
    return current, payload


def _candidate_semantics(plan: dict | None) -> list[dict] | None:
    if plan is None:
        return None
    rows = []
    for row in plan.get("sync_collect_candidates") or ():
        rows.append(
            {
                "candidate": row.get("candidate"),
                "safe": bool(row.get("safe")),
                "collected": bool(row.get("collected")),
                "reward_key": list(row.get("reward_key") or ()),
                "frames": int(row.get("frames", 0)),
            }
        )
    return rows


def _plan_semantics(plan: dict | None) -> dict | None:
    if plan is None:
        return None
    keys = (
        "candidate",
        "schedule",
        "score",
        "progress",
        "reward_prefix_safe",
        "reward_collected",
        "reward_key",
        "reward_target_dx",
        "reward_target_state",
        "reward_target_y",
        "reward_prefix_frames",
        "trajectory_event",
        "trajectory_safe_resolved",
        "trajectory_frames",
        "trajectory_end_x",
        "trajectory_end_y",
        "trajectory_reward_collected",
        "trajectory_target_reward_type",
    )
    result = {key: plan.get(key) for key in keys}
    result["candidates"] = _candidate_semantics(plan)
    return result


def _shutdown(core: MesenCore) -> None:
    v36._release_native_spec_runner()
    try:
        set_nes_controller_state(core, 0, 0)
    except Exception:
        pass
    try:
        core.stop()
    except Exception:
        pass
    try:
        core.release()
    except Exception:
        pass


def main() -> int:
    args = parse_args()
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest, state_file = _load_manifest(scenario_dir)

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()

    objective = StickyCollectObjective(ttl_frames=int(v35.v25._LIVE_OBJECTIVE.ttl_frames))
    objective.clear()
    compare_dir = Path("build/checkpoints/v36-native-star-compare")
    compare_dir.mkdir(parents=True, exist_ok=True)
    v35._SYNC_CHECKPOINT = (compare_dir / "v35-current.mss").resolve()
    v35._SYNC_STEP_TIMEOUT = float(args.step_timeout)

    try:
        _restore_root(core, state_file, manifest)
        current, radar = _collect_radar(core, objective)
        if v35._COLLECT_PROGRESS_CONTROL.target_type(radar) != "star":
            raise RuntimeError(
                "scenario root does not expose a Star COLLECT target; use a reward-visible fami-pixel scenario"
            )

        t0 = time.perf_counter()
        legacy = v35._sync_star_plan(
            core,
            current_frame=int(current.native_frame_id),
            live_radar=radar,
        )
        legacy_ms = (time.perf_counter() - t0) * 1000.0

        _restore_root(core, state_file, manifest)
        current2, radar2 = _collect_radar(core, StickyCollectObjective(ttl_frames=objective.ttl_frames))
        if int(current2.native_frame_id) != int(current.native_frame_id):
            raise RuntimeError("restored root frame changed between comparison passes")

        t1 = time.perf_counter()
        native = v36._sync_native_star_plan(
            core,
            current_frame=int(current2.native_frame_id),
            live_radar=radar2,
        )
        native_ms = (time.perf_counter() - t1) * 1000.0

        legacy_semantics = _plan_semantics(legacy)
        native_semantics = _plan_semantics(native)
        passed = legacy_semantics == native_semantics
        payload = {
            "schema": 1,
            "probe": "v36-native-star-semantic-compare",
            "scenario": str(scenario_dir),
            "root_frame": int(current.native_frame_id),
            "root_x": int(current.mario_x_abs),
            "pass": bool(passed),
            "legacy_ms": round(legacy_ms, 3),
            "native_ms": round(native_ms, 3),
            "speedup": None if native_ms <= 0 else legacy_ms / native_ms,
            "legacy": legacy_semantics,
            "native": native_semantics,
        }
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

        print("V35 vs V36 current-root Star semantic comparison")
        print(f"root       : frame={current.native_frame_id} X={current.mario_x_abs}")
        print(f"V35 legacy : {legacy_ms:.3f} ms")
        print(f"V36 native : {native_ms:.3f} ms")
        if native_ms > 0:
            print(f"speedup    : {legacy_ms / native_ms:.2f}x")
        print(f"semantics  : {'PASS' if passed else 'FAIL'}")
        print(f"JSON       : {output}")
        if passed:
            print(_DONE)
            return 0
        return 2
    finally:
        _shutdown(core)


if __name__ == "__main__":
    raise SystemExit(main())
