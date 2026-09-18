"""Pure COLLECT proof-selection and selected-result shaping helpers.

The helpers in this module do not own emulator state, IPC, response caches, or
authority-loop lifecycle.  They make the final proof-admission call explicit and
shape an already-selected proof into the live-plan payload consumed by control.
The caller supplies the authoritative lineage/proof selector and ledger.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Protocol


class CollectSelectionLike(Protocol):
    """Minimal result contract returned by the lineage/proof selector."""

    proof: dict | None
    valid_count: int
    rejected: Mapping[str, int]


CollectProofSelector = Callable[..., CollectSelectionLike]


def select_collect_proof(
    proofs: Iterable[Mapping],
    *,
    selector: CollectProofSelector,
    ledger: Any,
    current_frame: int,
    last_applied_generation: int,
    target_type: str,
    commit_frames: int,
    retention_frames: int,
) -> CollectSelectionLike:
    """Run the configured lineage/proof selector on detached proof payloads.

    The stable planning layer owns the admission call shape, while the supplied
    selector remains the authority for lineage matching, proof lease, safety,
    freshness, and reward ranking.
    """

    detached = [dict(proof) for proof in proofs]
    return selector(
        detached,
        ledger=ledger,
        current_frame=int(current_frame),
        last_applied_generation=int(last_applied_generation),
        target_type=str(target_type),
        commit_frames=int(commit_frames),
        retention_frames=int(retention_frames),
    )


def shape_eager_collect_result(
    selection: CollectSelectionLike,
    *,
    current_frame: int,
    live_radar: Mapping | None,
    handoff_frames: int,
) -> dict:
    """Shape a selected proof exactly as the eager V34 authority path expects."""

    if selection.proof is None:
        raise ValueError("cannot shape an empty COLLECT selection")

    result = dict(selection.proof)
    source_root = int(result["root_frame"])
    source_age = int(current_frame) - source_root
    handoff = int(handoff_frames)

    result["trajectory_root_frame"] = source_root
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = source_root
    result["age"] = source_age
    result["guard_mode"] = (
        f"collect-eager-handoff[{handoff}f,{result.get('candidate')},"
        f"src-age:{source_age}f,lease:{result.get('proof_remaining_frames')}f]"
    )
    result["live_radar"] = dict(live_radar or {})
    return result
