#!/usr/bin/env python3
"""V29 planner: cohort-coherent delay-compensated COLLECT.

V28 fixed stale COLLECT rebasing by adding scheduled 8f/12f handoffs, a current-
plan continuation anchor, branch-level reward proofs, and action-lineage/proof-
lease validation. One concurrency hole remained: authority could still consume a
COLLECT generation as soon as the first worker returned a valid proof. A fast
continuation anchor could therefore hide a stronger reward branch that arrived
later from the same root.

V29 adds the missing cohort contract without changing V28 worker trajectories:
worker responses are cached by `(generation, root_frame, worker)`, a COLLECT root
is rankable only after every configured worker has reported for that generation,
and only then are branch proofs filtered by lineage/lease and compared. A newer
partial cohort does not erase an older complete cohort; the older one may still
be selected only if its exact action lineage reaches current authority.
"""

from __future__ import annotations

from fami_pixel.games.smb1.collect_cohort import CollectResponseCache
from fami_pixel.games.smb1.collect_delay import select_lineage_collect_proof

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26
import mesen_smb_checkpoint_planner_v27 as v27
import mesen_smb_checkpoint_planner_v28 as v28


PLANNER_NAME = "v29-cohort-delay-collect"
_COLLECT_RESPONSE_CACHE = CollectResponseCache()


def _selected_collect_result(selection, *, target_type: str, current_frame: int, live_radar: dict) -> dict:
    result = dict(selection.proof)
    source_root_frame = int(result["root_frame"])
    source_age = int(current_frame) - source_root_frame
    result["trajectory_root_frame"] = source_root_frame
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = source_root_frame
    result["age"] = source_age
    result["guard_mode"] = (
        f"collect-cohort-lineage[{target_type},{result.get('candidate')},"
        f"src-age:{source_age}f,lease:{result.get('proof_remaining_frames')}f]"
    )
    result["live_radar"] = dict(live_radar or {})
    return result


def _best_collect_or_progress(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Wait for a coherent COLLECT worker cohort before exact branch ranking."""

    target_type = v25._collect_target_from_radar(live_radar)
    if target_type is None:
        return v27._best_forward_plan_partial_v27(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
            live_radar,
        )

    responses: list[dict] = []
    for path in response_paths:
        response = v11._read_json(path)
        if response is not None:
            responses.append(dict(response))

    retention = max(int(freshness), int(v28.COLLECT_PROOF_HORIZON))
    _COLLECT_RESPONSE_CACHE.ingest(
        responses,
        current_frame=current_frame,
        last_applied_generation=last_applied_generation,
        retention_frames=retention,
        target_type=target_type,
    )

    expected_workers = len(response_paths)
    newest_partial = None
    newest_complete_rejection = None
    for (generation, root_frame), cohort in _COLLECT_RESPONSE_CACHE.ordered_groups():
        workers = _COLLECT_RESPONSE_CACHE.worker_set(cohort)
        if newest_partial is None:
            newest_partial = (generation, root_frame, cohort, workers)
        if not _COLLECT_RESPONSE_CACHE.complete(cohort, expected_workers=expected_workers):
            continue

        selection = select_lineage_collect_proof(
            cohort,
            ledger=v27._AUTHORITY_ACTION_LEDGER,
            current_frame=current_frame,
            last_applied_generation=last_applied_generation,
            target_type=target_type,
            commit_frames=v23.EXECUTION_PREFIX_FRAMES,
            retention_frames=retention,
        )
        if selection.proof is None:
            if newest_complete_rejection is None:
                newest_complete_rejection = (
                    generation,
                    root_frame,
                    cohort,
                    workers,
                    selection,
                )
            continue

        result = _selected_collect_result(
            selection,
            target_type=target_type,
            current_frame=current_frame,
            live_radar=live_radar,
        )
        v23._latest_forward_meta = {
            "forward_model_status": "selected-cohort-lineage-collect-proof",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "collect_target_sticky": bool(live_radar.get("collect_target_sticky", False)),
            "forward_model_generation": int(generation),
            "forward_model_plan": result.get("candidate"),
            "forward_model_event": result.get("trajectory_event"),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "forward_model_simulated_frames": result.get("trajectory_frames"),
            "forward_model_end_x": result.get("trajectory_end_x"),
            "forward_model_end_y": result.get("trajectory_end_y"),
            "forward_model_reward_collected": result.get("reward_collected"),
            "forward_model_target_reward_type": target_type,
            "reward_target_dx": result.get("reward_target_dx"),
            "reward_target_state": result.get("reward_target_state"),
            "reward_target_y": result.get("reward_target_y"),
            "collect_handoff_frames": result.get("collect_handoff_frames"),
            "collect_continuation_anchor": result.get("collect_continuation_anchor"),
            "collect_lineage_matched_frames": result.get("lineage_matched_frames"),
            "collect_proof_remaining_frames": result.get("proof_remaining_frames"),
            "collect_cohort_size": len(cohort),
            "collect_worker_count": len(workers),
            "collect_expected_workers": int(expected_workers),
            "collect_lineage_valid_count": int(selection.valid_count),
            "collect_lineage_rejected": dict(selection.rejected),
        }
        return result

    if newest_partial is None:
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-collect-cohort",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "collect_expected_workers": int(expected_workers),
            "collect_worker_count": 0,
            "collect_cache_entries": len(_COLLECT_RESPONSE_CACHE),
        }
    elif newest_complete_rejection is not None:
        generation, root_frame, cohort, workers, selection = newest_complete_rejection
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-lineage-valid-collect-proof",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "collect_cohort_size": len(cohort),
            "collect_worker_count": len(workers),
            "collect_expected_workers": int(expected_workers),
            "collect_lineage_valid_count": int(selection.valid_count),
            "collect_lineage_rejected": dict(selection.rejected),
        }
    else:
        generation, root_frame, cohort, workers = newest_partial
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-collect-worker-quorum",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(current_frame) - int(root_frame),
            "collect_cohort_size": len(cohort),
            "collect_worker_count": len(workers),
            "collect_expected_workers": int(expected_workers),
            "collect_cache_entries": len(_COLLECT_RESPONSE_CACHE),
        }
    return None


def authority_main(args) -> int:
    _COLLECT_RESPONSE_CACHE.clear()
    v11._log(
        "Planner V29: coherent delayed COLLECT enabled | "
        "cache by generation/root/worker; wait for worker quorum before lineage + reward ranking"
    )
    return v28.authority_main(args)


def _install_v29_overrides() -> None:
    # V28 supplies the delayed reward worker, warm-start request, action ledger,
    # and V27 bounded PROGRESS path. Replace only the lower COLLECT selector.
    v28._install_v28_overrides()
    v26._BASE_V25_PLAN = _best_collect_or_progress

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v29_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
