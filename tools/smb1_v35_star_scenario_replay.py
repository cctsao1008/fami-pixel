#!/usr/bin/env python3
"""Deterministically replay V35's synchronous Star micro-MPC from one Mesen scenario.

This is the narrow acceptance gate for issue #36.  It does not enter World 1-1,
spawn shadow workers, or reuse historical live-run actions.  Instead it restores
an extracted reward-visible ``root.mss`` and repeatedly applies the exact V35
current-root Star policy:

    observe current native radar
      -> synchronously evaluate all V25 4f reward chunks on the live Mesen core
      -> restore the exact current root
      -> commit the selected 4f chunk
      -> reobserve and repeat

PASS requires native capability evidence: ``StarInvincibleTimer`` must increase
from the scenario-root baseline.  Power-up object disappearance is not accepted
as collection proof.
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
from fami_pixel.games.smb1 import (
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)
from fami_pixel.games.smb1.radar import read_smb1_radar


_EXAMPLES = (Path(__file__).resolve().parents[1] / "examples").resolve()
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

import mesen_smb_checkpoint_planner_v35 as v35  # noqa: E402


_DONE = "V35StarScenarioReplay: PASS"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay V35 current-root Star micro-MPC from an extracted Mesen scenario"
    )
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-v35-star-replay"))
    p.add_argument("--step-timeout", type=float, default=5.0)
    p.add_argument("--max-decisions", type=int, default=40)
    args = p.parse_args()
    if args.step_timeout <= 0:
        p.error("--step-timeout must be > 0")
    if args.max_decisions <= 0:
        p.error("--max-decisions must be > 0")
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
    expected_frame = int(manifest.get("native_frame"))
    expected_x = int(manifest.get("mario_x"))
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


def _radar(core: MesenCore, mario_x: int) -> dict:
    return read_smb1_radar(core, player_x=int(mario_x)).to_payload()


def _schedule_frames(schedule: list[dict] | tuple[dict, ...]):
    for segment in schedule:
        buttons = int(segment.get("buttons", 0))
        frames = int(segment.get("frames", 0))
        if frames <= 0:
            continue
        for _ in range(frames):
            yield buttons


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

    current = observation_from_state(core.frame_count(), read_smb1_state(core))
    root_radar = _radar(core, current.mario_x_abs)
    target_type = v35.v25._collect_target_from_radar(root_radar)
    if target_type != "star":
        raise SystemExit(
            "Star is not visible at the scenario root. This replay intentionally starts "
            "at the interception state. Re-extract the same event with "
            "--lead-generations 0 and without --stable-root."
        )

    baseline_timer = int(root_radar.get("star_invincible_timer", 0))
    scenario_id = str(manifest.get("id") or scenario_dir.name)
    replay_dir = Path("build/checkpoints/v35-star-scenario-replay") / scenario_id
    replay_dir.mkdir(parents=True, exist_ok=True)
    v35._SYNC_CHECKPOINT = (replay_dir / "sync-current.mss").resolve()
    v35._SYNC_STEP_TIMEOUT = float(args.step_timeout)

    reward = next(
        (
            item
            for item in (root_radar.get("rewards") or [])
            if str(item.get("type")) == "star"
        ),
        None,
    )
    print(f"Scenario   : {scenario_id}", flush=True)
    print(
        f"Root       : frame={current.native_frame_id} X={current.mario_x_abs} "
        f"Y={current.mario_y} star_dx={None if reward is None else reward.get('dx')} "
        f"star_state={None if reward is None else reward.get('state')}",
        flush=True,
    )
    print(f"Baseline   : StarInvincibleTimer={baseline_timer}", flush=True)
    print(
        "Policy     : V35 synchronous current-root 8x4f micro-MPC; commit one selected 4f chunk",
        flush=True,
    )

    previous = current
    try:
        for decision in range(int(args.max_decisions)):
            radar = _radar(core, current.mario_x_abs)
            timer_before = int(radar.get("star_invincible_timer", 0))
            if timer_before > baseline_timer:
                print(
                    f"PROOF      : StarInvincibleTimer={baseline_timer}->{timer_before} "
                    f"frame={current.native_frame_id} X={current.mario_x_abs}",
                    flush=True,
                )
                print(_DONE, flush=True)
                return 0

            if v35.v25._collect_target_from_radar(radar) != "star":
                raise RuntimeError(
                    "Star target disappeared before native invincibility proof; "
                    "object disappearance is not accepted as collection"
                )

            plan = v35._sync_star_plan(
                core,
                current_frame=int(current.native_frame_id),
                live_radar=radar,
            )
            if plan is None:
                raise RuntimeError("V35 returned no current-root Star plan")

            print(
                f"Decision {decision:02d}: frame={current.native_frame_id} "
                f"X={current.mario_x_abs} candidate={plan.get('candidate')} "
                f"predicted_collect={int(bool(plan.get('reward_collected', False)))} "
                f"target_dx={plan.get('reward_target_dx')} "
                f"compute={float(plan.get('compute_ms', 0.0)):.1f}ms",
                flush=True,
            )

            buttons_seq = tuple(_schedule_frames(plan.get("schedule") or ()))
            if not buttons_seq:
                raise RuntimeError(f"selected plan has an empty schedule: {plan.get('candidate')}")

            for buttons in buttons_seq:
                set_nes_controller_state(core, 0, int(buttons))
                core.step_frame_sync(1, max(1, int(float(args.step_timeout) * 1000.0)))
                state = read_smb1_state(core)
                current = observation_from_state(core.frame_count(), state)
                events = derive_game_events(previous, current)
                if any(event.kind == GameEventType.DIED for event in events):
                    raise RuntimeError(
                        f"Mario died during Star replay at frame={current.native_frame_id} "
                        f"X={current.mario_x_abs}"
                    )

                live = _radar(core, current.mario_x_abs)
                timer_now = int(live.get("star_invincible_timer", 0))
                if timer_now > baseline_timer:
                    print(
                        f"COLLECTED  : frame={current.native_frame_id} X={current.mario_x_abs} "
                        f"Y={current.mario_y} candidate={plan.get('candidate')}",
                        flush=True,
                    )
                    print(
                        f"PROOF      : StarInvincibleTimer={baseline_timer}->{timer_now}",
                        flush=True,
                    )
                    print(_DONE, flush=True)
                    return 0
                previous = current

        raise RuntimeError(
            f"Star was not collected within {int(args.max_decisions)} V35 decisions"
        )
    finally:
        try:
            set_nes_controller_state(core, 0, 0x00)
        except Exception:
            pass


def main() -> int:
    return worker(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
