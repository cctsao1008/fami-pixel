#!/usr/bin/env python3
"""Replay V36 native exact-boundary Star MPC from one fami-pixel scenario.

This is the end-to-end acceptance gate above the same reward-visible scenario
used by the V35 replay and V35-vs-V36 semantic comparison:

    current live root
      -> native exact-boundary 8x4f Star decision on separate spec Emulator
      -> commit exactly the live 4f prefix on authority
      -> reobserve sticky Star objective
      -> repeat until native StarInvincibleTimer proves collection

The tool records full decision latency. Collection proof remains raw SMB1 native
capability state; disappearance of the power-up object is never sufficient.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import time

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.games.smb1.reward_live import StickyCollectObjective
from fami_pixel.games.smb1.reward_target import read_active_reward_target


_EXAMPLES = (Path(__file__).resolve().parents[1] / "examples").resolve()
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

import mesen_smb_checkpoint_planner_v36 as v36  # noqa: E402


_DONE = "V36NativeStarScenarioReplay: PASS"
_LATENCY_TARGET_MS = 100.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay V36 native exact-boundary Star MPC from a fami-pixel Mesen scenario"
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("scenario_dir", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home-v36-star-replay"))
    parser.add_argument("--step-timeout", type=float, default=5.0)
    parser.add_argument("--max-decisions", type=int, default=40)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/v36-native-star-scenario-replay.json"),
    )
    args = parser.parse_args()
    if args.step_timeout <= 0:
        parser.error("--step-timeout must be > 0")
    if args.max_decisions <= 0:
        parser.error("--max-decisions must be > 0")
    return args


def _load_manifest(scenario_dir: Path) -> tuple[dict, Path]:
    manifest_path = scenario_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"scenario manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid scenario manifest: {manifest_path}: {exc}") from exc
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


def _native_radar(core: MesenCore, mario_x: int) -> dict:
    return read_smb1_radar(core, player_x=int(mario_x)).to_payload()


def _collect_radar(
    core: MesenCore,
    mario_x: int,
    objective: StickyCollectObjective,
) -> dict:
    payload = _native_radar(core, mario_x)
    try:
        tracked = read_active_reward_target(core, player_x=int(mario_x))
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
    return payload


def _schedule_frames(schedule: list[dict] | tuple[dict, ...]):
    for segment in schedule:
        buttons = int(segment.get("buttons", 0))
        frames = int(segment.get("frames", 0))
        if frames <= 0:
            continue
        for _ in range(frames):
            yield buttons


def _commit_prefix_buttons(
    schedule: list[dict] | tuple[dict, ...],
    commit_frames: int,
) -> tuple[int, ...]:
    count = int(commit_frames)
    if count <= 0:
        raise ValueError("commit_frames must be > 0")
    expanded = tuple(_schedule_frames(schedule))
    if len(expanded) < count:
        raise RuntimeError(
            f"selected schedule proves only {len(expanded)}f, shorter than {count}f live commit"
        )
    return expanded[:count]


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * float(q)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _latency_summary(values_ms: list[float]) -> dict:
    if not values_ms:
        return {
            "samples": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
            "target_ms": _LATENCY_TARGET_MS,
            "target_pass": False,
        }
    p50 = _percentile(values_ms, 0.50)
    return {
        "samples": len(values_ms),
        "mean_ms": statistics.fmean(values_ms),
        "p50_ms": p50,
        "p95_ms": _percentile(values_ms, 0.95),
        "max_ms": max(values_ms),
        "target_ms": _LATENCY_TARGET_MS,
        "target_pass": bool(p50 is not None and p50 < _LATENCY_TARGET_MS),
    }


def _shutdown_core(core: MesenCore) -> None:
    v36._release_native_spec_runner()
    try:
        set_nes_controller_state(core, 0, 0x00)
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


def _write_payload(output: Path, payload: dict) -> None:
    target = output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest, root_state = _load_manifest(scenario_dir)

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()
    _restore_root(core, root_state, manifest)

    objective = StickyCollectObjective(
        ttl_frames=int(v36.v35.v25._LIVE_OBJECTIVE.ttl_frames)
    )
    objective.clear()
    commit_frames = int(v36.v35.v23.EXECUTION_PREFIX_FRAMES)

    current = observation_from_state(core.frame_count(), read_smb1_state(core))
    root_radar = _collect_radar(core, current.mario_x_abs, objective)
    if v36.v35._COLLECT_PROGRESS_CONTROL.target_type(root_radar) != "star":
        raise SystemExit(
            "Star is not visible at the scenario root. Use the same reward-visible "
            "fami-pixel scenario accepted by the V35/V36 comparison gate."
        )

    baseline_timer = int(root_radar.get("star_invincible_timer", 0))
    scenario_id = str(manifest.get("id") or scenario_dir.name)
    decisions: list[dict] = []
    decision_times_ms: list[float] = []

    print(f"Scenario   : {scenario_id}", flush=True)
    print(
        f"Root       : frame={current.native_frame_id} X={current.mario_x_abs} "
        f"Y={current.mario_y}",
        flush=True,
    )
    print(f"Baseline   : StarInvincibleTimer={baseline_timer}", flush=True)
    print(
        "Policy     : V36 native exact-boundary 8x4f Star MPC; "
        f"commit={commit_frames}f; latency target p50<{_LATENCY_TARGET_MS:.0f}ms",
        flush=True,
    )

    previous = current
    try:
        for decision_index in range(int(args.max_decisions)):
            radar = _collect_radar(core, current.mario_x_abs, objective)
            timer_before = int(radar.get("star_invincible_timer", 0))
            if timer_before > baseline_timer:
                summary = _latency_summary(decision_times_ms)
                payload = {
                    "schema": 1,
                    "probe": "v36-native-star-scenario-replay",
                    "scenario": str(scenario_dir),
                    "scenario_id": scenario_id,
                    "root_frame": int(manifest["native_frame"]),
                    "root_x": int(manifest["mario_x"]),
                    "collection_proven": True,
                    "collection_frame": int(current.native_frame_id),
                    "collection_x": int(current.mario_x_abs),
                    "baseline_star_timer": baseline_timer,
                    "final_star_timer": timer_before,
                    "decisions": decisions,
                    "latency": summary,
                }
                _write_payload(args.output, payload)
                print(
                    f"PROOF      : StarInvincibleTimer={baseline_timer}->{timer_before} "
                    f"frame={current.native_frame_id} X={current.mario_x_abs}",
                    flush=True,
                )
                print(
                    f"Latency    : p50={summary['p50_ms']:.3f} ms "
                    f"p95={summary['p95_ms']:.3f} ms "
                    f"target={'PASS' if summary['target_pass'] else 'FAIL'}",
                    flush=True,
                )
                print(f"JSON       : {args.output.expanduser().resolve()}", flush=True)
                print(_DONE, flush=True)
                return 0

            if v36.v35._COLLECT_PROGRESS_CONTROL.target_type(radar) != "star":
                raise RuntimeError(
                    "Sticky Star objective expired before native invincibility proof; "
                    f"frame={current.native_frame_id} X={current.mario_x_abs} "
                    f"nearest_reward_type={radar.get('nearest_reward_type')} "
                    f"sticky={radar.get('collect_target_sticky')}"
                )

            started = time.perf_counter()
            plan = v36._sync_native_star_plan(
                core,
                current_frame=int(current.native_frame_id),
                live_radar=radar,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            decision_times_ms.append(elapsed_ms)
            if plan is None:
                raise RuntimeError("V36 returned no native current-root Star plan")
            if plan.get("sync_collect_engine") != "native-exact-boundary":
                raise RuntimeError(
                    "V36 replay unexpectedly left the native exact-boundary engine: "
                    f"{plan.get('sync_collect_engine')}"
                )

            row = {
                "decision": int(decision_index),
                "frame": int(current.native_frame_id),
                "x": int(current.mario_x_abs),
                "candidate": plan.get("candidate"),
                "predicted_collect": bool(plan.get("reward_collected", False)),
                "target_dx": plan.get("reward_target_dx"),
                "sticky": bool(radar.get("collect_target_sticky", False)),
                "compute_ms": elapsed_ms,
            }
            decisions.append(row)
            print(
                f"Decision {decision_index:02d}: frame={current.native_frame_id} "
                f"X={current.mario_x_abs} candidate={plan.get('candidate')} "
                f"predicted_collect={int(bool(plan.get('reward_collected', False)))} "
                f"target_dx={plan.get('reward_target_dx')} "
                f"sticky={int(bool(radar.get('collect_target_sticky', False)))} "
                f"compute={elapsed_ms:.1f}ms",
                flush=True,
            )

            buttons_seq = _commit_prefix_buttons(
                plan.get("schedule") or (),
                commit_frames,
            )
            for buttons in buttons_seq:
                set_nes_controller_state(core, 0, int(buttons))
                core.step_frame_sync(
                    1,
                    max(1, int(float(args.step_timeout) * 1000.0)),
                )
                state = read_smb1_state(core)
                current = observation_from_state(core.frame_count(), state)
                events = derive_game_events(previous, current)
                if any(event.kind == GameEventType.DIED for event in events):
                    raise RuntimeError(
                        f"Mario died during V36 Star replay at frame={current.native_frame_id} "
                        f"X={current.mario_x_abs}"
                    )

                live = _native_radar(core, current.mario_x_abs)
                timer_now = int(live.get("star_invincible_timer", 0))
                if timer_now > baseline_timer:
                    summary = _latency_summary(decision_times_ms)
                    payload = {
                        "schema": 1,
                        "probe": "v36-native-star-scenario-replay",
                        "scenario": str(scenario_dir),
                        "scenario_id": scenario_id,
                        "root_frame": int(manifest["native_frame"]),
                        "root_x": int(manifest["mario_x"]),
                        "collection_proven": True,
                        "collection_frame": int(current.native_frame_id),
                        "collection_x": int(current.mario_x_abs),
                        "baseline_star_timer": baseline_timer,
                        "final_star_timer": timer_now,
                        "decisions": decisions,
                        "latency": summary,
                    }
                    _write_payload(args.output, payload)
                    print(
                        f"COLLECTED  : frame={current.native_frame_id} X={current.mario_x_abs} "
                        f"Y={current.mario_y} candidate={plan.get('candidate')}",
                        flush=True,
                    )
                    print(
                        f"PROOF      : StarInvincibleTimer={baseline_timer}->{timer_now}",
                        flush=True,
                    )
                    print(
                        f"Latency    : p50={summary['p50_ms']:.3f} ms "
                        f"p95={summary['p95_ms']:.3f} ms "
                        f"target={'PASS' if summary['target_pass'] else 'FAIL'}",
                        flush=True,
                    )
                    print(f"JSON       : {args.output.expanduser().resolve()}", flush=True)
                    print(_DONE, flush=True)
                    return 0
                previous = current

        summary = _latency_summary(decision_times_ms)
        payload = {
            "schema": 1,
            "probe": "v36-native-star-scenario-replay",
            "scenario": str(scenario_dir),
            "scenario_id": scenario_id,
            "root_frame": int(manifest["native_frame"]),
            "root_x": int(manifest["mario_x"]),
            "collection_proven": False,
            "baseline_star_timer": baseline_timer,
            "decisions": decisions,
            "latency": summary,
        }
        _write_payload(args.output, payload)
        raise RuntimeError(
            f"Star collection was not proven within {args.max_decisions} V36 decisions"
        )
    finally:
        _shutdown_core(core)


def main() -> int:
    return worker(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
