#!/usr/bin/env python3
"""V32 planner: deadline-closed coherent COLLECT cohorts.

V29 removed first-finisher policy by waiting for every worker in a COLLECT cohort.
That is deterministic but can starve when one worker misses the useful handoff
window. V28's delayed schedules already define a stronger real-time fact: a new
branch is normally adoptable only before its scheduled handoff. If authority
continues the old plan past that point, lineage validation rejects the branch.

V32 therefore treats the latest handoff (currently 12f) as the cohort deadline:

- before the deadline, a generation is rankable only with full worker quorum;
- at/after the deadline, the cohort is explicitly closed with the responses that
  have arrived, then lineage/lease filtering decides what is still reachable;
- newer partial cohorts do not hide older complete/deadline-closed cohorts;
- late workers after a consumed deadline are intentionally ignored rather than
  accidentally changing policy.

This converts accidental response latency into an explicit real-time deadline.
V31's shared-prefix worker tree and recursion-safe V28 fallback remain unchanged.
"""

from __future__ import annotations

from fami_pixel.games.smb1.collect_delay import select_lineage_collect_proof

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v25 as v25
import mesen_smb_checkpoint_planner_v26 as v26
import mesen_smb_checkpoint_planner_v27 as v27
import mesen_smb_checkpoint_planner_v28 as v28
import mesen_smb_checkpoint_planner_v29 as v29
import mesen_smb_checkpoint_planner_v31 as v31


PLANNER_NAME = "v32-deadline-cohort-shared-collect"


def _cohort_deadline_frames() -> int:
    return max(int(value) for value in v28.COLLECT_HANDOFF_FRAMES)


def _selected_collect_result(selection, *, target_type: str, current_frame: int, live_radar: dict) -> dict:
    result = dict(selection.proof)
    source_root_frame = int(result["root_frame"])
    source_age = int(current_frame) - source_root_frame
    result["trajectory_root_frame"] = source_root_frame
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = source_root_frame
    result["age"] = source_age
    result["guard_mode"] = (
        f"collect-deadline-lineage[{target_type},{result.get('candidate')},"
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
    """Require quorum before deadline; close partial cohorts at the last handoff."""

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
    v29._COLLECT_RESPONSE_CACHE.ingest(
        responses,
        current_frame=current_frame,
        last_applied_generation=last_applied_generation,
        retention_frames=retention,
        target_type=target_type,
    )

    expected_workers = len(response_paths)
    expected_set = set(range(expected_workers))
    deadline = _cohort_deadline_frames()
    newest_waiting = None
    newest_closed_rejection = None

    for (generation, root_frame), cohort in v29._COLLECT_RESPONSE_CACHE.ordered_groups():
        workers = v29._COLLECT_RESPONSE_CACHE.worker_set(cohort)
        age = int(current_frame) - int(root_frame)
        complete = v29._COLLECT_RESPONSE_CACHE.complete(
            cohort,
            expected_workers=expected_workers,
        )
        deadline_closed = age >= int(deadline)
        if newest_waiting is None:
            newest_waiting = (generation, root_frame, cohort, workers, age, complete)
        if not complete and not deadline_closed:
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
        missing_workers = sorted(expected_set - workers)
        if selection.proof is None:
            if newest_closed_rejection is None:
                newest_closed_rejection = (
                    generation,
                    root_frame,
                    cohort,
                    workers,
                    age,
                    complete,
                    missing_workers,
                    selection,
                )
            continue

        result = _selected_collect_result(
            selection,
            target_type=target_type,
            current_frame=current_frame,
            live_radar=live_radar,
        )
        result["collect_cohort_complete"] = bool(complete)
        result["collect_deadline_closed"] = bool(not complete and deadline_closed)
        result["collect_missing_workers"] = list(missing_workers)
        result["collect_deadline_frames"] = int(deadline)

        v23._latest_forward_meta = {
            "forward_model_status": (
                "selected-cohort-lineage-collect-proof"
                if complete
                else "selected-deadline-closed-collect-proof"
            ),
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "collect_target_sticky": bool(live_radar.get("collect_target_sticky", False)),
            "forward_model_generation": int(generation),
            "forward_model_plan": result.get("candidate"),
            "forward_model_event": result.get("trajectory_event"),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": age,
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
            "collect_missing_workers": list(missing_workers),
            "collect_cohort_complete": bool(complete),
            "collect_deadline_frames": int(deadline),
            "collect_deadline_closed": bool(not complete and deadline_closed),
            "collect_lineage_valid_count": int(selection.valid_count),
            "collect_lineage_rejected": dict(selection.rejected),
        }
        return result

    if newest_waiting is None:
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-collect-cohort",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "collect_expected_workers": int(expected_workers),
            "collect_worker_count": 0,
            "collect_deadline_frames": int(deadline),
            "collect_cache_entries": len(v29._COLLECT_RESPONSE_CACHE),
        }
    elif newest_closed_rejection is not None:
        (
            generation,
            root_frame,
            cohort,
            workers,
            age,
            complete,
            missing_workers,
            selection,
        ) = newest_closed_rejection
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-lineage-valid-collect-proof",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(age),
            "collect_cohort_size": len(cohort),
            "collect_worker_count": len(workers),
            "collect_expected_workers": int(expected_workers),
            "collect_missing_workers": list(missing_workers),
            "collect_cohort_complete": bool(complete),
            "collect_deadline_frames": int(deadline),
            "collect_deadline_closed": bool(not complete and age >= deadline),
            "collect_lineage_valid_count": int(selection.valid_count),
            "collect_lineage_rejected": dict(selection.rejected),
        }
    else:
        generation, root_frame, cohort, workers, age, complete = newest_waiting
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-collect-worker-quorum",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_generation": int(generation),
            "forward_model_source_frame": int(root_frame),
            "forward_model_source_age_frames": int(age),
            "collect_cohort_size": len(cohort),
            "collect_worker_count": len(workers),
            "collect_expected_workers": int(expected_workers),
            "collect_missing_workers": sorted(expected_set - workers),
            "collect_cohort_complete": bool(complete),
            "collect_deadline_frames": int(deadline),
            "collect_deadline_closed": False,
            "collect_cache_entries": len(v29._COLLECT_RESPONSE_CACHE),
        }
    return None


def authority_main(args) -> int:
    v29._COLLECT_RESPONSE_CACHE.clear()
    v11._log(
        "Planner V32: deadline-closed COLLECT cohorts enabled | "
        f"full quorum before {_cohort_deadline_frames()}f; close partial cohort at latest useful handoff"
    )
    return v31.v30.authority_main(args)


def _install_v32_overrides() -> None:
    v31._install_v31_overrides()

    # V26 is the SURVIVE/landing owner. Replace only the lower COLLECT/PROGRESS
    # delegate; PROGRESS still goes through V27 from inside this selector.
    v26._BASE_V25_PLAN = _best_collect_or_progress

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v32_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
