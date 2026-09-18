"""Stable eager-COLLECT authority orchestration.

This module owns the control-flow that turns already-read worker responses into one
COLLECT authority decision.  It intentionally does not own emulator execution,
worker IPC, candidate generation, or the lineage/proof policy itself.  Those are
supplied by the caller or remain in lower stable planning/domain layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

from fami_pixel.planning import (
    collect_anchor_proofs,
    evaluate_handoff_stage,
    select_collect_proof,
    shape_eager_collect_result,
)

from .collect_status import (
    selected_collect_anchor_meta,
    selected_eager_collect_meta,
    waiting_collect_cohort_meta,
    waiting_eager_handoff_meta,
    waiting_lineage_collect_meta,
)


class CollectResponseCache(Protocol):
    """Minimal cache contract required by eager COLLECT orchestration."""

    def ingest(
        self,
        responses: Sequence[Mapping],
        *,
        current_frame: int,
        last_applied_generation: int,
        retention_frames: int,
        target_type: str,
    ) -> None: ...

    def ordered_groups(self): ...

    def handoff_snapshot(self, *, generation: int, root_frame: int): ...


@dataclass(frozen=True)
class EagerCollectDecision:
    """Plan plus telemetry metadata resulting from one authority scan."""

    plan: dict | None
    meta: dict


def select_eager_collect_decision(
    responses: Sequence[Mapping],
    *,
    cache: CollectResponseCache,
    handoff_frames: Iterable[int],
    current_frame: int,
    last_applied_generation: int,
    target_type: str,
    live_radar: Mapping | None,
    retention_frames: int,
    active_workers: Iterable[int],
    proof_selector,
    ledger: Any,
    commit_frames: int,
) -> EagerCollectDecision:
    """Run the eager +handoff COLLECT authority scan.

    The ordering is preserved from V34:

    1. ingest currently visible worker responses;
    2. scan cached roots newest-first according to the cache;
    3. within each root, inspect handoffs earliest-to-latest;
    4. block later handoffs while the earliest stage is still open/incomplete;
    5. run lineage/proof admission only for rankable stages;
    6. after reward stages are exhausted, consider continuation anchors;
    7. return the selected plan or the exact waiting/rejection status metadata.
    """

    target = str(target_type)
    current = int(current_frame)
    last_generation = int(last_applied_generation)
    retention = int(retention_frames)
    commit = int(commit_frames)
    handoffs = tuple(int(value) for value in handoff_frames)
    active = set(int(worker) for worker in active_workers)

    cache.ingest(
        [dict(response) for response in responses],
        current_frame=current,
        last_applied_generation=last_generation,
        retention_frames=retention,
        target_type=target,
    )

    newest_wait = None
    newest_rejection = None

    for (generation, root_frame), cohort in cache.ordered_groups():
        generation = int(generation)
        root_frame = int(root_frame)
        age = current - root_frame
        handoff_timing = cache.handoff_snapshot(
            generation=generation,
            root_frame=root_frame,
        )

        for handoff in handoffs:
            stage = evaluate_handoff_stage(
                cohort,
                handoff_frames=handoff,
                age_frames=age,
                active_workers=active,
            )
            proofs = [dict(proof) for proof in stage.proofs]
            workers = set(stage.workers)

            if stage.waiting:
                newest_wait = (
                    generation,
                    root_frame,
                    handoff,
                    age,
                    workers,
                    handoff_timing,
                )
                break
            if not stage.rankable:
                continue

            selection = select_collect_proof(
                proofs,
                selector=proof_selector,
                ledger=ledger,
                current_frame=current,
                last_applied_generation=last_generation,
                target_type=target,
                commit_frames=commit,
                retention_frames=retention,
            )
            if selection.proof is None:
                newest_rejection = (
                    generation,
                    root_frame,
                    handoff,
                    age,
                    workers,
                    selection,
                    handoff_timing,
                )
                continue

            result = shape_eager_collect_result(
                selection,
                current_frame=current,
                live_radar=live_radar,
                handoff_frames=handoff,
            )
            return EagerCollectDecision(
                plan=result,
                meta=selected_eager_collect_meta(
                    target_type=target,
                    generation=generation,
                    root_frame=root_frame,
                    age_frames=age,
                    result=result,
                    handoff_frames=handoff,
                    stage_complete=stage.complete,
                    stage_closed=stage.closed,
                    workers=workers,
                    active_workers=active,
                    valid_count=selection.valid_count,
                    rejected=selection.rejected,
                    handoff_timing=handoff_timing,
                ),
            )
        else:
            anchors = collect_anchor_proofs(cohort)
            if anchors:
                selection = select_collect_proof(
                    anchors,
                    selector=proof_selector,
                    ledger=ledger,
                    current_frame=current,
                    last_applied_generation=last_generation,
                    target_type=target,
                    commit_frames=commit,
                    retention_frames=retention,
                )
                if selection.proof is not None:
                    anchor_handoff = int(
                        selection.proof.get("collect_handoff_frames", 0)
                    )
                    result = shape_eager_collect_result(
                        selection,
                        current_frame=current,
                        live_radar=live_radar,
                        handoff_frames=anchor_handoff,
                    )
                    return EagerCollectDecision(
                        plan=result,
                        meta=selected_collect_anchor_meta(
                            target_type=target,
                            generation=generation,
                            root_frame=root_frame,
                            age_frames=age,
                            result=result,
                            handoff_timing=handoff_timing,
                        ),
                    )
            continue

        # The earliest open stage for this root blocks all later handoffs and
        # all older roots, exactly as the V34 authority path did.
        if newest_wait is not None:
            break

    if newest_wait is not None:
        generation, root_frame, handoff, age, workers, timing = newest_wait
        meta = waiting_eager_handoff_meta(
            target_type=target,
            generation=generation,
            root_frame=root_frame,
            age_frames=age,
            handoff_frames=handoff,
            workers=workers,
            active_workers=active,
            handoff_timing=timing,
        )
    elif newest_rejection is not None:
        generation, root_frame, handoff, age, workers, selection, timing = newest_rejection
        meta = waiting_lineage_collect_meta(
            target_type=target,
            generation=generation,
            root_frame=root_frame,
            age_frames=age,
            handoff_frames=handoff,
            workers=workers,
            rejected=selection.rejected,
            handoff_timing=timing,
        )
    else:
        meta = waiting_collect_cohort_meta(target_type=target)

    return EagerCollectDecision(plan=None, meta=meta)
