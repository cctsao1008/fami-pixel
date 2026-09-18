"""Pure COLLECT handoff admission primitives.

These helpers contain no emulator, filesystem, IPC, or authority-loop behavior.
They only interpret already-ingested worker response payloads and the configured
handoff policy. Exact Mesen proof validity, action lineage, and proof lease remain
separate authorities applied by the caller after a handoff stage becomes rankable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence


WorkerHasWork = Callable[[int, int], bool]


@dataclass(frozen=True)
class CollectHandoffStage:
    """One handoff stage after response-cache admission has already occurred."""

    handoff_frames: int
    age_frames: int
    proofs: tuple[dict, ...]
    workers: frozenset[int]
    active_workers: frozenset[int]
    complete: bool
    closed: bool

    @property
    def waiting(self) -> bool:
        """Whether this earliest stage must block consideration of later stages."""

        if not self.proofs:
            return not self.closed
        return not self.complete and not self.closed

    @property
    def rankable(self) -> bool:
        """Whether available proofs may proceed to lineage/lease ranking."""

        return bool(self.proofs) and (self.complete or self.closed)


def ordered_handoffs(handoffs: Iterable[int]) -> tuple[int, ...]:
    """Return a validated earliest-to-latest handoff sequence.

    Handoff order is behavioral policy. The caller must provide a positive,
    strictly increasing sequence; this helper intentionally does not silently
    sort or deduplicate a malformed configuration.
    """

    values = tuple(int(value) for value in handoffs)
    if not values:
        raise ValueError("at least one handoff is required")
    if any(value <= 0 for value in values):
        raise ValueError("handoff frames must be > 0")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise ValueError("handoff frames must be strictly increasing")
    return values


def active_worker_ids(worker_count: int, *, has_work: WorkerHasWork) -> set[int]:
    """Return workers that own at least one candidate in the current shard map."""

    total = max(1, int(worker_count))
    return {worker for worker in range(total) if bool(has_work(worker, total))}


def collect_handoff_proofs(
    cohort: Sequence[Mapping],
    handoff_frames: int,
) -> tuple[list[dict], set[int]]:
    """Extract non-anchor branch proofs for exactly one handoff stage."""

    handoff = int(handoff_frames)
    proofs: list[dict] = []
    workers: set[int] = set()
    for response in cohort:
        raw = response.get("branch_proofs")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            if bool(item.get("collect_continuation_anchor", False)):
                continue
            try:
                item_handoff = int(item.get("collect_handoff_frames", -1))
                worker = int(item.get("worker", response.get("worker", -1)))
            except (TypeError, ValueError):
                continue
            if item_handoff != handoff or worker < 0:
                continue
            proofs.append(dict(item))
            workers.add(worker)
    return proofs, workers


def collect_anchor_proofs(cohort: Sequence[Mapping]) -> list[dict]:
    """Extract continuation-anchor proofs from an admitted response cohort."""

    anchors: list[dict] = []
    for response in cohort:
        raw = response.get("branch_proofs")
        if not isinstance(raw, list):
            continue
        anchors.extend(
            dict(item)
            for item in raw
            if isinstance(item, dict) and bool(item.get("collect_continuation_anchor", False))
        )
    return anchors


def evaluate_handoff_stage(
    cohort: Sequence[Mapping],
    *,
    handoff_frames: int,
    age_frames: int,
    active_workers: Iterable[int],
) -> CollectHandoffStage:
    """Classify one stage as waiting, rankable, or exhausted.

    Before the handoff deadline, incomplete worker coverage blocks later stages.
    At/after the handoff, the stage closes with whatever admitted proofs exist.
    An empty closed stage is exhausted rather than rankable, allowing the caller
    to continue to the next configured handoff.
    """

    handoff = int(handoff_frames)
    age = int(age_frames)
    proofs, workers = collect_handoff_proofs(cohort, handoff)
    active = frozenset(int(worker) for worker in active_workers)
    worker_set = frozenset(workers)
    return CollectHandoffStage(
        handoff_frames=handoff,
        age_frames=age,
        proofs=tuple(dict(proof) for proof in proofs),
        workers=worker_set,
        active_workers=active,
        complete=active.issubset(worker_set),
        closed=age >= handoff,
    )
