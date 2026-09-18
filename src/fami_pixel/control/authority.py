"""Stable live-authority loop orchestration.

The current SMB1 planner historically executed its authoritative per-frame loop
inside the V17 example module.  This module owns that control flow without owning
SMB1 policy implementations: concrete planner, radar, checkpoint, worker, and
telemetry providers are injected by the composition root.

The loop preserves the existing authority contract:

- live Mesen advances independently of asynchronous planning;
- only a currently selected schedule is applied to live authority;
- planner requests are published after an exact live checkpoint;
- response freshness/generation policy remains in the injected selector;
- worker/viewer cleanup is best-effort and cannot rewrite terminal authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Callable

from fami_pixel.control.request_enrichment import enrich_checkpoint_request
from fami_pixel.games.smb1 import GameEventType


@dataclass(frozen=True)
class LiveAuthorityControl:
    """Injected composition for one authoritative live-control loop."""

    prepare_run: Callable[[Any], Path]
    create_recorder: Callable[[], Any]
    spawn_workers: Callable[[Any, Path, list[Path]], list[Any]]
    core_factory: Callable[[Any], Any]
    configure_controller: Callable[[Any, int], None]
    enter_world: Callable[[Any, float], Any]
    observe_state: Callable[[Any, Any], Any]
    read_state: Callable[[Any], Any]
    derive_events: Callable[[Any, Any], Any]
    set_controller_state: Callable[[Any, int, int], Any]
    step_core: Callable[[Any, float], Any]
    read_radar: Callable[..., Any]
    radar_reason: Callable[[dict], Any]
    select_plan: Callable[[list[Path], int, int, int, dict], dict | None]
    looks_grounded: Callable[[Any], bool]
    emergency_jump_plan: Callable[[int, dict], dict]
    schedule_buttons: Callable[..., int]
    schedule_label: Callable[[str], str]
    save_checkpoint: Callable[[Any, Path], tuple[int, int, int]]
    publish_json: Callable[[Path, dict], bool]
    append_timeline: Callable[[Any, dict], None]
    persist_terminal: Callable[..., None]
    viewer_factory: Callable[..., Any]
    format_radar_strip: Callable[..., str]
    log: Callable[[str], None]
    bootstrap_schedule: Any
    jump_names: Any
    radar_lookahead_px: int
    enemy_trigger_px: int
    gap_trigger_px: int
    obstacle_trigger_px: int
    risk_cutoff: Callable[[], float]
    ui_stride: int = 2

    def run(self, args: Any) -> int:
        """Run one live authority session with historical V17 semantics."""

        model_path = self.prepare_run(args)
        recorder = self.create_recorder()

        checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        request_path = checkpoint_dir / "request.json"
        response_paths = [
            checkpoint_dir / f"response-{i}.json"
            for i in range(args.shadow_workers)
        ]
        for stale in [request_path, *response_paths]:
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

        workers = self.spawn_workers(args, request_path, response_paths)

        core = self.core_factory(args.dll)
        core.initialize_headless(args.home)
        self.configure_controller(core, 1)
        if not core.load_rom(args.rom):
            for worker in workers:
                worker.terminate()
            return 2
        core.initialize_debugger()
        state = self.enter_world(core, args.step_timeout)
        current = self.observe_state(core, state)
        previous = current

        viewer = None
        if args.web_ui:
            viewer = self.viewer_factory(port=args.web_port, playback_fps=30.0)
            viewer.start()
            self.log(f"Web UI    : {viewer.url}")

        self.log(
            "Planner V17: live radar + reactive jump + observable evidence | "
            f"control={args.control_quantum}f freshness={args.plan_freshness}f "
            f"enemy={self.enemy_trigger_px}px gap={self.gap_trigger_px}px "
            f"obstacle={self.obstacle_trigger_px}px risk_cutoff={self.risk_cutoff():g}"
        )
        self.log(f"Surrogate : {model_path}")

        applied_schedule = self.bootstrap_schedule
        applied_label = "BOOTSTRAP PULSE-JUMP"
        applied_plan_root = current.native_frame_id
        using_bootstrap = True
        generation = 0
        last_applied_generation = -1
        last_plan_root = -1
        last_plan_age = None
        last_plan_compute_ms = None
        last_guard_mode = "bootstrap"
        last_risk = None
        last_stall = None
        live_radar_payload: dict = {}
        radar_reason = None

        try:
            for loop_index in range(args.max_frames):
                schedule_age = max(0, current.native_frame_id - applied_plan_root)
                applied_buttons = self.schedule_buttons(
                    applied_schedule,
                    schedule_age,
                    repeat=using_bootstrap,
                )

                self.set_controller_state(core, 0, applied_buttons)
                self.step_core(core, args.step_timeout)
                state = self.read_state(core)
                current = self.observe_state(core, state)
                events = self.derive_events(previous, current)

                if viewer is not None and loop_index % self.ui_stride == 0:
                    viewer.publish_core(
                        core,
                        current,
                        decision=generation,
                        mode="LIVE RADAR + EVIDENCE",
                        action=applied_label,
                        metadata={
                            "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                            "plan_age": last_plan_age,
                            "planner_state": f"live-radar/parallel-{args.shadow_workers}",
                            "plan_compute_ms": last_plan_compute_ms,
                            "guard_mode": last_guard_mode,
                            "risk_probability": last_risk,
                            "no_progress_probability": last_stall,
                            "radar_enemy_dx": live_radar_payload.get("nearest_enemy_dx"),
                            "radar_gap_dx": live_radar_payload.get("nearest_gap_dx"),
                            "radar_obstacle_dx": live_radar_payload.get("nearest_obstacle_dx"),
                            "radar_reason": radar_reason,
                            "radar_strip": self.format_radar_strip(
                                live_radar_payload.get("nearest_enemy_dx"),
                                live_radar_payload.get("nearest_gap_dx"),
                                live_radar_payload.get("nearest_obstacle_dx"),
                                lookahead_px=self.radar_lookahead_px,
                            ),
                            "hazard_ahead": radar_reason is not None,
                        },
                    )

                if any(event.kind == GameEventType.DIED for event in events):
                    self.persist_terminal(
                        recorder,
                        terminal="death",
                        core=core,
                        current=current,
                        live_radar_payload=live_radar_payload,
                        args=args,
                        model_path=model_path,
                        applied_label=applied_label,
                        last_plan_root=last_plan_root,
                        last_plan_age=last_plan_age,
                        last_plan_compute_ms=last_plan_compute_ms,
                        last_guard_mode=last_guard_mode,
                        last_risk=last_risk,
                        last_stall=last_stall,
                    )
                    self.log(
                        f"PlannerV11: FAIL death | frame={current.native_frame_id} "
                        f"X={current.mario_x_abs}"
                    )
                    return 6

                if any(event.kind == GameEventType.LEVEL_COMPLETED for event in events):
                    self.persist_terminal(
                        recorder,
                        terminal="level_complete",
                        core=core,
                        current=current,
                        live_radar_payload=live_radar_payload,
                        args=args,
                        model_path=model_path,
                        applied_label=applied_label,
                        last_plan_root=last_plan_root,
                        last_plan_age=last_plan_age,
                        last_plan_compute_ms=last_plan_compute_ms,
                        last_guard_mode=last_guard_mode,
                        last_risk=last_risk,
                        last_stall=last_stall,
                    )
                    self.log(
                        f"PlannerV11: PASS level complete | frame={current.native_frame_id} "
                        f"X={current.mario_x_abs}"
                    )
                    return 0

                if loop_index % args.control_quantum == 0:
                    live_radar_payload = self.read_radar(
                        core,
                        player_x=current.mario_x_abs,
                        lookahead_px=self.radar_lookahead_px,
                    ).to_payload()
                    radar_reason = self.radar_reason(live_radar_payload)

                    plan = self.select_plan(
                        response_paths,
                        current.native_frame_id,
                        args.plan_freshness,
                        last_applied_generation,
                        live_radar_payload,
                    )

                    if radar_reason is not None and self.looks_grounded(current):
                        if plan is None or str(plan.get("candidate")) not in self.jump_names:
                            plan = self.emergency_jump_plan(
                                current.native_frame_id,
                                live_radar_payload,
                            )

                    if plan is not None:
                        applied_schedule = list(plan.get("schedule") or [])
                        applied_label = self.schedule_label(str(plan["candidate"]))
                        applied_plan_root = int(plan["root_frame"])
                        using_bootstrap = False
                        plan_generation = int(plan.get("generation", -1))
                        if plan_generation >= 0:
                            last_applied_generation = plan_generation
                        last_plan_root = applied_plan_root
                        last_plan_age = int(plan.get("age", 0))
                        last_plan_compute_ms = float(plan.get("compute_ms", 0.0))
                        last_guard_mode = str(plan.get("guard_mode", "none"))
                        last_risk = float(plan.get("risk_probability", 0.0))
                        last_stall = float(plan.get("no_progress_probability", 0.0))

                        enemy = live_radar_payload.get("nearest_enemy_dx")
                        gap = live_radar_payload.get("nearest_gap_dx")
                        obstacle = live_radar_payload.get("nearest_obstacle_dx")
                        self.log(
                            f"control update: {applied_label} root={last_plan_root} "
                            f"age={last_plan_age}f worker={plan.get('worker')} "
                            f"compute={last_plan_compute_ms:.1f}ms "
                            f"radar=e:{enemy}/g:{gap}/o:{obstacle} "
                            f"risk={last_risk:.3f} guard={last_guard_mode}"
                        )

                    self.append_timeline(
                        recorder,
                        {
                            "generation": generation,
                            "native_frame": int(current.native_frame_id),
                            "mario_x": int(current.mario_x_abs),
                            "mario_y": int(current.mario_y),
                            "action": applied_label,
                            "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                            "plan_age_frames": last_plan_age,
                            "plan_compute_ms": last_plan_compute_ms,
                            "guard_mode": last_guard_mode,
                            "risk_probability": last_risk,
                            "no_progress_probability": last_stall,
                            "radar_reason": radar_reason,
                            "radar": live_radar_payload,
                        },
                    )

                    generation += 1
                    checkpoint = checkpoint_dir / f"live-{generation:06d}.mss"
                    frame, x, engine = self.save_checkpoint(core, checkpoint)
                    request_payload = enrich_checkpoint_request(
                        {
                            "generation": generation,
                            "checkpoint": str(checkpoint),
                            "frame": frame,
                            "x": x,
                            "engine": engine,
                            "radar": live_radar_payload,
                        }
                    )
                    published = self.publish_json(request_path, request_payload)
                    if not published:
                        self.log(
                            f"IPC backpressure: dropped planner snapshot generation={generation} "
                            "after transient Windows sharing conflicts"
                        )

                    keep = max(6, (args.plan_freshness // args.control_quantum) + 6)
                    obsolete_generation = generation - keep
                    if obsolete_generation > 0:
                        try:
                            (checkpoint_dir / f"live-{obsolete_generation:06d}.mss").unlink()
                        except FileNotFoundError:
                            pass

                previous = current

            self.persist_terminal(
                recorder,
                terminal="frame_limit",
                core=core,
                current=current,
                live_radar_payload=live_radar_payload,
                args=args,
                model_path=model_path,
                applied_label=applied_label,
                last_plan_root=last_plan_root,
                last_plan_age=last_plan_age,
                last_plan_compute_ms=last_plan_compute_ms,
                last_guard_mode=last_guard_mode,
                last_risk=last_risk,
                last_stall=last_stall,
            )
            self.log(
                f"PlannerV11: FAIL frame limit | frame={current.native_frame_id} "
                f"X={current.mario_x_abs}"
            )
            return 7
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.terminate()
            for worker in workers:
                try:
                    worker.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    worker.kill()
            try:
                self.set_controller_state(core, 0, 0x00)
            except Exception:
                pass
            if viewer is not None:
                try:
                    viewer.stop()
                except Exception:
                    pass
