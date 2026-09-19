"""Pure PROGRESS proof selection for the current authority path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Collection, Mapping, Sequence


AnchorSelector = Callable[[list[dict]], set[str]]
LineageValidator = Callable[..., tuple[list[dict], dict[str, int]]]


@dataclass(frozen=True)
class ProgressSelection:
    plan: dict | None
    meta: dict


def select_progress_proof(
    groups: Mapping[tuple[int, int], Sequence[dict]],
    *,
    current_frame: int,
    required_anchors: Collection[str],
    evaluated_anchors: AnchorSelector,
    lineage_validator: LineageValidator,
    live_radar: Mapping | None,
) -> ProgressSelection:
    """Select the newest reachable complete PROGRESS cohort.

    Ordering, anchor quorum, lineage fallback, ranking, and telemetry mirror the
    historical V27 selector. Cache intake and lineage proof mechanics remain
    injected dependencies so this function stays pure and emulator-agnostic.
    """

    required = frozenset(required_anchors)
    ordered = sorted(
        groups.items(),
        key=lambda item: (item[0][1], item[0][0]),
        reverse=True,
    )

    newest_partial = None
    newest_complete_rejection = None
    for (generation, root_frame), raw_proofs in ordered:
        proofs = [dict(proof) for proof in raw_proofs]
        anchors = set(evaluated_anchors(proofs))
        if newest_partial is None:
            newest_partial = (generation, root_frame, proofs, anchors)
        if not required.issubset(anchors):
            continue

        lineage_valid, rejected = lineage_validator(
            proofs,
            current_frame=int(current_frame),
        )
        if not lineage_valid:
            if newest_complete_rejection is None:
                newest_complete_rejection = (
                    generation,
                    root_frame,
                    proofs,
                    anchors,
                    dict(rejected),
                )
            continue

        selected = max(lineage_valid, key=lambda item: tuple(item.get("score") or ()))
        result = dict(selected)
        source_root_frame = int(result["root_frame"])
        source_age = int(current_frame) - source_root_frame
        result["trajectory_root_frame"] = source_root_frame
        result["trajectory_source_age_frames"] = source_age
        result["root_frame"] = source_root_frame
        result["age"] = source_age
        result["cohort_generation"] = int(generation)
        result["cohort_size"] = len(proofs)
        result["guard_mode"] = (
            "forward-model-lineage-cohort["
            f"{result.get('trajectory_event')},"
            f"src-age:{source_age}f,"
            f"lease:{result.get('proof_remaining_frames')}f,"
            f"proofs:{len(proofs)},"
            f"anchors:{','.join(sorted(anchors))}]"
        )
        result["live_radar"] = dict(live_radar or {})

        meta = {
            "forward_model_status": "selected-lineage-cohort",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_plan": result.get("candidate"),
            "forward_model_event": result.get("trajectory_event"),
            "forward_model_source_frame": source_root_frame,
            "forward_model_source_age_frames": source_age,
            "forward_model_simulated_frames": result.get("trajectory_frames"),
            "forward_model_progress": result.get("progress"),
            "forward_model_end_x": result.get("trajectory_end_x"),
            "forward_model_end_y": result.get("trajectory_end_y"),
            "search_depth": result.get("search_depth"),
            "search_generated": result.get("search_generated"),
            "search_pruned": result.get("search_pruned"),
            "search_top_k": result.get("search_top_k"),
            "search_selected": result.get("search_selected"),
            "search_response_count": len(proofs),
            "search_lineage_valid_count": len(lineage_valid),
            "search_lineage_rejected": dict(rejected),
            "search_required_anchors": sorted(required),
            "search_evaluated_anchors": sorted(anchors),
            "lineage_matched_frames": result.get("lineage_matched_frames"),
            "proof_remaining_frames": result.get("proof_remaining_frames"),
            "surrogate_predicted_delta_x": result.get("surrogate_predicted_delta_x"),
            "surrogate_rank_key": result.get("surrogate_rank_key"),
            "surrogate_anchor": result.get("surrogate_anchor"),
        }
        return ProgressSelection(plan=result, meta=meta)

    if newest_partial is None:
        meta = {
            "forward_model_status": "waiting-ranked-cohort",
            "objective_mode": "PROGRESS",
            "search_response_count": 0,
            "search_required_anchors": sorted(required),
            "search_evaluated_anchors": [],
        }
    elif newest_complete_rejection is not None:
        generation, root_frame, proofs, anchors, rejected = newest_complete_rejection
        meta = {
            "forward_model_status": "waiting-lineage-valid-proof",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "search_response_count": len(proofs),
            "search_required_anchors": sorted(required),
            "search_evaluated_anchors": sorted(anchors),
            "search_lineage_rejected": dict(rejected),
        }
    else:
        generation, root_frame, proofs, anchors = newest_partial
        meta = {
            "forward_model_status": "waiting-ranked-anchor-quorum",
            "objective_mode": "PROGRESS",
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "search_response_count": len(proofs),
            "search_required_anchors": sorted(required),
            "search_evaluated_anchors": sorted(anchors),
        }
    return ProgressSelection(plan=None, meta=meta)
