"""Stable COLLECT authority-status metadata shapers.

These helpers preserve the current V34 telemetry/status payload contract without
owning planner policy, emulator state, IPC, or response-cache lifecycle.  They
only shape already-decided authority outcomes into the metadata consumed by the
existing telemetry/UI path.
"""

from __future__ import annotations

from typing import Iterable, Mapping


def selected_eager_collect_meta(
    *,
    target_type: str,
    generation: int,
    root_frame: int,
    age_frames: int,
    result: Mapping,
    handoff_frames: int,
    stage_complete: bool,
    stage_closed: bool,
    workers: Iterable[int],
    active_workers: Iterable[int],
    valid_count: int,
    rejected: Mapping[str, int],
    handoff_timing,
) -> dict:
    return {
        "forward_model_status": "selected-eager-handoff-collect-proof",
        "objective_mode": "COLLECT",
        "collect_target_type": str(target_type),
        "forward_model_generation": int(generation),
        "forward_model_plan": result.get("candidate"),
        "forward_model_event": result.get("trajectory_event"),
        "forward_model_source_frame": int(root_frame),
        "forward_model_source_age_frames": int(age_frames),
        "forward_model_reward_collected": result.get("reward_collected"),
        "collect_handoff_frames": int(handoff_frames),
        "collect_handoff_stage_complete": bool(stage_complete),
        "collect_handoff_stage_closed": bool(stage_closed),
        "collect_handoff_workers": sorted(int(worker) for worker in workers),
        "collect_active_workers": sorted(int(worker) for worker in active_workers),
        "collect_lineage_valid_count": int(valid_count),
        "collect_lineage_rejected": dict(rejected),
        "collect_proof_remaining_frames": result.get("proof_remaining_frames"),
        "collect_handoff_timing": handoff_timing,
    }


def selected_collect_anchor_meta(
    *,
    target_type: str,
    generation: int,
    root_frame: int,
    age_frames: int,
    result: Mapping,
    handoff_timing,
) -> dict:
    return {
        "forward_model_status": "selected-collect-continuation-anchor",
        "objective_mode": "COLLECT",
        "collect_target_type": str(target_type),
        "forward_model_generation": int(generation),
        "forward_model_plan": result.get("candidate"),
        "forward_model_source_frame": int(root_frame),
        "forward_model_source_age_frames": int(age_frames),
        "collect_handoff_timing": handoff_timing,
    }


def waiting_eager_handoff_meta(
    *,
    target_type: str,
    generation: int,
    root_frame: int,
    age_frames: int,
    handoff_frames: int,
    workers: Iterable[int],
    active_workers: Iterable[int],
    handoff_timing,
) -> dict:
    return {
        "forward_model_status": "waiting-eager-collect-handoff",
        "objective_mode": "COLLECT",
        "collect_target_type": str(target_type),
        "forward_model_generation": int(generation),
        "forward_model_source_frame": int(root_frame),
        "forward_model_source_age_frames": int(age_frames),
        "collect_waiting_handoff_frames": int(handoff_frames),
        "collect_handoff_workers": sorted(int(worker) for worker in workers),
        "collect_active_workers": sorted(int(worker) for worker in active_workers),
        "collect_handoff_timing": handoff_timing,
    }


def waiting_lineage_collect_meta(
    *,
    target_type: str,
    generation: int,
    root_frame: int,
    age_frames: int,
    handoff_frames: int,
    workers: Iterable[int],
    rejected: Mapping[str, int],
    handoff_timing,
) -> dict:
    return {
        "forward_model_status": "waiting-lineage-valid-eager-collect-proof",
        "objective_mode": "COLLECT",
        "collect_target_type": str(target_type),
        "forward_model_generation": int(generation),
        "forward_model_source_frame": int(root_frame),
        "forward_model_source_age_frames": int(age_frames),
        "collect_handoff_frames": int(handoff_frames),
        "collect_handoff_workers": sorted(int(worker) for worker in workers),
        "collect_lineage_rejected": dict(rejected),
        "collect_handoff_timing": handoff_timing,
    }


def waiting_collect_cohort_meta(*, target_type: str) -> dict:
    return {
        "forward_model_status": "waiting-eager-collect-cohort",
        "objective_mode": "COLLECT",
        "collect_target_type": str(target_type),
    }
