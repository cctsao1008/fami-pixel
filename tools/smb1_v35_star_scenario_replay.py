#!/usr/bin/env python3
"""Deterministically replay V35's synchronous Star micro-MPC from one Mesen scenario.

This is the narrow acceptance gate for issue #36. It does not enter World 1-1,
spawn shadow workers, or reuse historical live-run actions. Instead it restores
an extracted reward-visible ``root.mss`` and repeatedly applies the exact V35
current-root Star policy:

    observe current native radar + V25 sticky COLLECT objective
      -> synchronously evaluate all V25 4f reward chunks on the live Mesen core
      -> restore the exact current root
      -> commit exactly the first live control quantum (4f)
      -> reobserve and repeat

V25/V35 schedules intentionally append a 1-frame A-released continuation tail so
live control remains safe if no replacement plan arrives. The normal V17/V35
loop replans after the 4-frame control quantum, so this deterministic harness
must not consume that fallback tail as part of a normal decision.

PASS requires native capability evidence: ``StarInvincibleTimer`` must increase
from the scenario-root baseline. Power-up object disappearance alone is never
accepted as collection proof.

The sticky objective is important for fidelity with the live V35 authority path.
The ordinary forward radar may temporarily stop reporting a selected Star after
a braking/backtracking prefix, while V25 deliberately keeps the COLLECT target
alive for a bounded TTL using ``read_active_reward_target``. The replay mirrors
that control-quantum behavior instead of treating one raw-radar dropout as a
terminal target loss.
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
from fami_pixel.games.smb1.reward_live import StickyCollectObjective
from fami_pixel.games.smb1.reward_target import read_active_reward_target


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


def _native_radar(core: MesenCore, mario_x: int) -> dict:
    return read_smb1_radar(core, player_x=int(mario_x)).to_payload()


def _collect_radar(
    core: MesenCore,
    mario_x: int,
    objective: StickyCollectObjective,
) -> dict:
    """Mirror V25's authority-side sticky COLLECT radar at a control boundary."""

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
    """Return exactly the live commit prefix, excluding any fallback tail."""

    count = int(commit_frames)
    if count <= 0:
        raise ValueError("commit_frames must be > 0")
    expanded = tuple(_schedule_frames(schedule))
    if len(expanded) < count:
        raise RuntimeError(
            f"selected schedule proves only {len(expanded)}f, shorter than {count}f live commit"
        )
    return expanded[:count]


def _shutdown_core(core: MesenCore) -> None:
    """Best-effort native teardown; never let one cleanup failure skip Release()."""

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

    objective = StickyCollectObjective(ttl_frames=int(v35.v25._LIVE_OBJECTIVE.ttl_frames))
    objective.clear()
    commit_frames = int(v35.v23.EXECUTION_PREFIX_FRAMES)

    current = observation_from_state(core.frame_count(), read_smb1_state(core))
    root_radar = _collect_radar(core, current.mario_x_abs, objective)
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
        "Policy     : V35 synchronous current-root 8x4f micro-MPC; "
        f"commit exactly {commit_frames}f then replan; fallback release tail not consumed; "
        f"sticky_ttl={objective.ttl_frames}f",
        flush=True,
    )

    previous = current
    try:
        for decision in range(int(args.max_decisions)):
            radar = _collect_radar(core, current.mario_x_abs, objective)
            timer_before = int(radar.get("star_invincible_timer", 0))
            if timer_before > baseline_timer:
                print(
                    f"PROOF      : StarInvincibleTimer={baseline_timer}->{timer_before} "
                    f"frame={current.native_frame_id} X={current.mario_x_abs}",
                    flush=True,
                )
                print(_DONE, flush=True)
                return 0

            target_type = v35.v25._collect_target_from_radar(radar)
            if target_type != "star":
                raise RuntimeError(
                    "Sticky Star objective expired before native invincibility proof; "
                    f"frame={current.native_frame_id} X={current.mario_x_abs} "
                    f"nearest_reward_type={radar.get('nearest_reward_type')} "
                    f"sticky={radar.get('collect_target_sticky')}"
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
                f"sticky={int(bool(radar.get('collect_target_sticky', False)))} "
                f"compute={float(plan.get('compute_ms', 0.0)):.1f}ms",
                flush=True,
            )

            buttons_seq = _commit_prefix_buttons(
                plan.get("schedule") or (),
                commit_frames,
            )

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

                # Collection proof is always raw/native capability state. Do not
                # update the sticky objective here; the live authority updates it
                # once per control quantum, not once per committed frame.
                live = _native_radar(core, current.mario_x_abs)
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
        _shutdown_core(core)


def main() -> int:
    return worker(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())