"""Action-lineage contracts for asynchronous SMB1 Mesen branch proofs.

An exact Mesen result is rooted in a historical authoritative state. Once the
live authority has advanced, source age alone does not make that proof reusable:
the authority must have executed exactly the same input prefix as the candidate.
If the lineages diverge, the simulated branch no longer describes the current
state and must be rejected rather than rebased to age zero.

This module deliberately knows nothing about candidate ranking. It answers only
whether a branch proof is still reachable from the live authority history and
whether enough exact-Mesen horizon remains to cover the next commitment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BranchProofValidation:
    valid: bool
    reason: str
    source_age: int
    matched_frames: int
    proof_remaining_frames: int
    mismatch_frame: int | None = None
    expected_buttons: int | None = None
    actual_buttons: int | None = None


class AuthorityActionLedger:
    """Bounded frame->buttons ledger for actions actually applied by authority.

    A key ``f`` records the final NES buttons used for the authoritative
    transition ``f -> f+1``. Re-recording the same frame replaces the value,
    which matches controller semantics if a caller changes the pad state more
    than once before stepping the emulator.
    """

    def __init__(self, *, max_entries: int = 512) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self.max_entries = int(max_entries)
        self._buttons: dict[int, int] = {}

    def clear(self) -> None:
        self._buttons.clear()

    def record(self, frame: int, buttons: int) -> None:
        frame_id = int(frame)
        self._buttons[frame_id] = int(buttons)
        overflow = len(self._buttons) - self.max_entries
        if overflow > 0:
            for old_frame in sorted(self._buttons)[:overflow]:
                del self._buttons[old_frame]

    def buttons_between(self, start_frame: int, end_frame: int) -> tuple[int, ...] | None:
        """Return actions for ``[start_frame, end_frame)`` or None on a gap."""

        start = int(start_frame)
        end = int(end_frame)
        if end < start:
            raise ValueError("end_frame must be >= start_frame")
        values: list[int] = []
        for frame in range(start, end):
            if frame not in self._buttons:
                return None
            values.append(int(self._buttons[frame]))
        return tuple(values)

    def __len__(self) -> int:
        return len(self._buttons)


def schedule_buttons_at(schedule: list[dict] | tuple[dict, ...], elapsed_frames: int) -> int | None:
    """Return the schedule action at an age, holding the final segment afterward."""

    if not schedule:
        return None
    remaining = max(0, int(elapsed_frames))
    last_buttons: int | None = None
    for segment in schedule:
        try:
            frames = int(segment["frames"])
            buttons = int(segment["buttons"])
        except (KeyError, TypeError, ValueError):
            return None
        if frames <= 0:
            return None
        last_buttons = buttons
        if remaining < frames:
            return buttons
        remaining -= frames
    return last_buttons


def validate_branch_proof(
    proof: dict,
    *,
    ledger: AuthorityActionLedger,
    current_frame: int,
    commit_frames: int,
    safety_field: str = "trajectory_safe_resolved",
) -> BranchProofValidation:
    """Validate reachability and remaining exact proof for one stale branch.

    Validity contract::

        actual_buttons[root:current] == candidate_buttons[0:source_age]
        trajectory_frames - source_age >= commit_frames
        proof[safety_field] is true

    PROGRESS proofs normally use ``trajectory_safe_resolved``. Bounded COLLECT
    proofs use ``reward_prefix_safe`` because an exact alive reward prefix may
    intentionally terminate at a finite proof horizon without a terminal game
    event. The caller chooses the semantic safety bit; lineage and lease rules
    are identical.
    """

    if commit_frames <= 0:
        raise ValueError("commit_frames must be > 0")
    if not safety_field:
        raise ValueError("safety_field must be non-empty")

    try:
        root_frame = int(proof["root_frame"])
        trajectory_frames = int(proof.get("trajectory_frames", 0))
    except (KeyError, TypeError, ValueError):
        return BranchProofValidation(False, "malformed-proof", 0, 0, 0)

    source_age = int(current_frame) - root_frame
    proof_remaining = trajectory_frames - source_age
    if source_age < 0:
        return BranchProofValidation(
            False, "future-root", source_age, 0, proof_remaining
        )
    if not bool(proof.get(safety_field, False)):
        return BranchProofValidation(
            False, "not-safe-resolved", source_age, 0, proof_remaining
        )
    if proof_remaining < int(commit_frames):
        return BranchProofValidation(
            False, "proof-lease-expired", source_age, 0, proof_remaining
        )

    schedule = proof.get("schedule") or ()
    if not schedule:
        return BranchProofValidation(
            False, "missing-schedule", source_age, 0, proof_remaining
        )

    actual = ledger.buttons_between(root_frame, int(current_frame))
    if actual is None:
        return BranchProofValidation(
            False, "authority-ledger-gap", source_age, 0, proof_remaining
        )

    for offset, actual_buttons in enumerate(actual):
        expected_buttons = schedule_buttons_at(schedule, offset)
        if expected_buttons is None:
            return BranchProofValidation(
                False,
                "malformed-schedule",
                source_age,
                offset,
                proof_remaining,
                mismatch_frame=root_frame + offset,
                actual_buttons=actual_buttons,
            )
        if int(actual_buttons) != int(expected_buttons):
            return BranchProofValidation(
                False,
                "lineage-mismatch",
                source_age,
                offset,
                proof_remaining,
                mismatch_frame=root_frame + offset,
                expected_buttons=int(expected_buttons),
                actual_buttons=int(actual_buttons),
            )

    return BranchProofValidation(
        True,
        "lineage-match",
        source_age,
        source_age,
        proof_remaining,
    )
