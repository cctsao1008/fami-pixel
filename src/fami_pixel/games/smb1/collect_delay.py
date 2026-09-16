"""Delay-compensated COLLECT helpers for asynchronous SMB1 control.

A reward branch computed from an old checkpoint cannot simply be replayed from
age zero when the response arrives. The live authority has already executed
some inputs. Delayed COLLECT therefore uses the same exact action-lineage and
proof-lease contract as PROGRESS, plus a scheduled handoff:

    current-plan continuation -> reward chunk -> A-released tail

If the result arrives before the handoff, authority may adopt the historical
proof at its real phase and continue toward the planned branch point. If it
arrives after the handoff, action-lineage validation accepts it only when live
authority actually executed the same reward branch. A continuation-only anchor
keeps one exact candidate aligned with the current plan while workers compute.
"""

from __future__ import annotations

from dataclasses import dataclass

from .action_lineage import (
    AuthorityActionLedger,
    schedule_buttons_at,
    validate_branch_proof,
)


DEFAULT_COLLECT_HANDOFF_FRAMES = (8, 12)
DEFAULT_COLLECT_PROOF_HORIZON = 24


@dataclass(frozen=True)
class CollectSelection:
    proof: dict | None
    valid_count: int
    rejected: dict[str, int]
    inspected_groups: int


def compress_button_frames(buttons: list[int] | tuple[int, ...]) -> list[dict[str, int]]:
    """Run-length encode exact NES button bytes as a planner schedule."""

    if not buttons:
        return []
    schedule: list[dict[str, int]] = []
    current = int(buttons[0])
    frames = 1
    for raw in buttons[1:]:
        value = int(raw)
        if value == current:
            frames += 1
            continue
        schedule.append({"buttons": current, "frames": frames})
        current = value
        frames = 1
    schedule.append({"buttons": current, "frames": frames})
    return schedule


def schedule_window(
    schedule: list[dict] | tuple[dict, ...],
    *,
    start_age: int,
    frames: int,
) -> list[dict[str, int]]:
    """Materialize an exact finite schedule window, holding the final segment."""

    if start_age < 0:
        raise ValueError("start_age must be >= 0")
    if frames <= 0:
        raise ValueError("frames must be > 0")
    values: list[int] = []
    for offset in range(int(frames)):
        value = schedule_buttons_at(schedule, int(start_age) + offset)
        if value is None:
            return []
        values.append(int(value))
    return compress_button_frames(values)


def compose_delayed_collect_schedule(
    continuation_schedule: list[dict] | tuple[dict, ...],
    reward_schedule: list[dict] | tuple[dict, ...],
    *,
    handoff_frames: int,
) -> list[dict[str, int]]:
    """Prefix a reward maneuver with the exact current-plan continuation."""

    if handoff_frames < 0:
        raise ValueError("handoff_frames must be >= 0")
    if not reward_schedule:
        raise ValueError("reward_schedule must be non-empty")
    if handoff_frames == 0:
        return [dict(segment) for segment in reward_schedule]

    prefix = schedule_window(
        continuation_schedule,
        start_age=0,
        frames=int(handoff_frames),
    )
    if not prefix:
        return []
    values: list[int] = []
    for segment in prefix:
        values.extend([int(segment["buttons"])] * int(segment["frames"]))
    for segment in reward_schedule:
        values.extend([int(segment["buttons"])] * int(segment["frames"]))
    return compress_button_frames(values)


def continuation_anchor_schedule(
    continuation_schedule: list[dict] | tuple[dict, ...],
    *,
    proof_horizon: int = DEFAULT_COLLECT_PROOF_HORIZON,
) -> list[dict[str, int]]:
    """Materialize the current-plan continuation for an exact proof horizon."""

    return schedule_window(
        continuation_schedule,
        start_age=0,
        frames=int(proof_horizon),
    )


def flatten_collect_proofs(responses: list[dict] | tuple[dict, ...]) -> list[dict]:
    """Expand worker branch bundles while preserving response identity fields."""

    proofs: list[dict] = []
    for response in responses:
        if not response or "error" in response:
            continue
        raw = response.get("branch_proofs")
        items = raw if isinstance(raw, list) and raw else [response]
        for item in items:
            if not isinstance(item, dict):
                continue
            proof = dict(item)
            for key in ("generation", "worker", "root_frame", "planner_mode", "target_reward_type"):
                proof.setdefault(key, response.get(key))
            proofs.append(proof)
    return proofs


def collect_proof_rank_key(proof: dict) -> tuple:
    """Reward-first ordering after lineage validity has already been established."""

    collected = 1 if bool(proof.get("reward_collected", False)) else 0
    raw = proof.get("reward_key") or ()
    try:
        reward_key = tuple(int(value) for value in raw)
    except (TypeError, ValueError):
        reward_key = ()
    # Prefer an earlier handoff only after authoritative reward geometry ties.
    try:
        handoff = int(proof.get("collect_handoff_frames", 1_000_000))
    except (TypeError, ValueError):
        handoff = 1_000_000
    return (collected, reward_key, -handoff, str(proof.get("candidate") or ""))


def select_lineage_collect_proof(
    responses: list[dict] | tuple[dict, ...],
    *,
    ledger: AuthorityActionLedger,
    current_frame: int,
    last_applied_generation: int,
    target_type: str,
    commit_frames: int,
    retention_frames: int,
) -> CollectSelection:
    """Select the newest reachable delayed COLLECT proof.

    Root/generation recency determines which coherent proof cohort is considered
    first. Within a cohort, exact action lineage and remaining proof lease filter
    candidates before reward ranking. A high-scoring but unreachable branch can
    never beat a lower-scoring branch that still reaches the current state.
    """

    if retention_frames < 0:
        raise ValueError("retention_frames must be >= 0")

    groups: dict[tuple[int, int], list[dict]] = {}
    rejected: dict[str, int] = {}
    for proof in flatten_collect_proofs(responses):
        if str(proof.get("planner_mode")) != "collect":
            continue
        if str(proof.get("target_reward_type")) != str(target_type):
            continue
        try:
            generation = int(proof.get("generation", -1))
            root_frame = int(proof.get("root_frame", -1))
        except (TypeError, ValueError):
            rejected["malformed-proof"] = rejected.get("malformed-proof", 0) + 1
            continue
        age = int(current_frame) - root_frame
        if generation <= int(last_applied_generation):
            rejected["consumed-generation"] = rejected.get("consumed-generation", 0) + 1
            continue
        if age < 0:
            rejected["future-root"] = rejected.get("future-root", 0) + 1
            continue
        if age > int(retention_frames):
            rejected["retention-expired"] = rejected.get("retention-expired", 0) + 1
            continue
        groups.setdefault((root_frame, generation), []).append(proof)

    ordered = sorted(groups.items(), key=lambda item: item[0], reverse=True)
    inspected = 0
    total_valid = 0
    for (_root_frame, _generation), proofs in ordered:
        inspected += 1
        valid: list[dict] = []
        for proof in proofs:
            validation = validate_branch_proof(
                proof,
                ledger=ledger,
                current_frame=current_frame,
                commit_frames=commit_frames,
                safety_field="reward_prefix_safe",
            )
            if not validation.valid:
                rejected[validation.reason] = rejected.get(validation.reason, 0) + 1
                continue
            candidate = dict(proof)
            candidate["trajectory_source_age_frames"] = int(validation.source_age)
            candidate["lineage_matched_frames"] = int(validation.matched_frames)
            candidate["proof_remaining_frames"] = int(validation.proof_remaining_frames)
            candidate["lineage_validation"] = validation.reason
            valid.append(candidate)
        total_valid += len(valid)
        if valid:
            return CollectSelection(
                proof=max(valid, key=collect_proof_rank_key),
                valid_count=total_valid,
                rejected=rejected,
                inspected_groups=inspected,
            )

    return CollectSelection(
        proof=None,
        valid_count=total_valid,
        rejected=rejected,
        inspected_groups=inspected,
    )
