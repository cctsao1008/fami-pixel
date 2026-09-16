"""Shared-prefix budgeting for delay-compensated COLLECT search.

V28/V29 make asynchronous reward proofs semantically exact, but the naive worker
implementation replays the same authority continuation from the root for every
reward branch.  The continuation is deterministic and identical until the
scheduled handoff, so it can be simulated once and checkpointed at each handoff.

This module is deliberately emulator-independent.  It describes the exact frame
budget of that tree and derives the minimum proof horizon required to cover a
bounded source-age assumption plus the next authority commitment.
"""

from __future__ import annotations

from dataclasses import dataclass


def minimum_proof_horizon(*, max_source_age: int, commit_frames: int) -> int:
    """Return the smallest root-relative proof horizon that covers one commit."""

    if max_source_age < 0:
        raise ValueError("max_source_age must be >= 0")
    if commit_frames <= 0:
        raise ValueError("commit_frames must be > 0")
    return int(max_source_age) + int(commit_frames)


@dataclass(frozen=True)
class CollectTreeBudget:
    """Exact-step budget for one root with shared continuation checkpoints.

    ``chunk_count`` is the total reward vocabulary across all workers.  The model
    assumes the deterministic continuation trunk is evaluated once globally,
    handoff checkpoints are shared by worker processes, and one continuation-only
    anchor extends from the latest handoff to the proof horizon.
    """

    chunk_count: int
    handoffs: tuple[int, ...]
    proof_horizon: int
    include_continuation_anchor: bool = True

    def __post_init__(self) -> None:
        if self.chunk_count <= 0:
            raise ValueError("chunk_count must be > 0")
        if not self.handoffs:
            raise ValueError("handoffs must be non-empty")
        if any(int(value) < 0 for value in self.handoffs):
            raise ValueError("handoffs must be >= 0")
        if tuple(sorted(set(int(value) for value in self.handoffs))) != tuple(
            int(value) for value in self.handoffs
        ):
            raise ValueError("handoffs must be unique and sorted")
        if self.proof_horizon <= max(self.handoffs):
            raise ValueError("proof_horizon must extend past every handoff")

    @property
    def reward_branch_count(self) -> int:
        return int(self.chunk_count) * len(self.handoffs)

    @property
    def proof_count(self) -> int:
        return self.reward_branch_count + (1 if self.include_continuation_anchor else 0)

    @property
    def naive_exact_steps(self) -> int:
        """Root replay cost when every proof independently simulates the horizon."""

        return self.proof_count * int(self.proof_horizon)

    @property
    def shared_trunk_steps(self) -> int:
        return max(int(value) for value in self.handoffs)

    @property
    def reward_suffix_steps(self) -> int:
        horizon = int(self.proof_horizon)
        return int(self.chunk_count) * sum(horizon - int(value) for value in self.handoffs)

    @property
    def anchor_suffix_steps(self) -> int:
        if not self.include_continuation_anchor:
            return 0
        return int(self.proof_horizon) - self.shared_trunk_steps

    @property
    def shared_exact_steps(self) -> int:
        return self.shared_trunk_steps + self.reward_suffix_steps + self.anchor_suffix_steps

    @property
    def saved_exact_steps(self) -> int:
        return self.naive_exact_steps - self.shared_exact_steps

    @property
    def reduction_ratio(self) -> float:
        if self.naive_exact_steps <= 0:
            return 0.0
        return self.saved_exact_steps / self.naive_exact_steps
