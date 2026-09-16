#!/usr/bin/env python3
"""V33 planner: authority-observed latency gate for COLLECT cohorts.

V32 introduced a 12-frame cohort deadline, but the deadline was recomputed from
whatever happened to be present in the response cache on each later control
quantum.  If a partial cohort reached the deadline without a usable proof, a
worker first observed at age 16 could still be ingested later and retroactively
change that supposedly closed cohort.

V33 makes deadline membership irreversible.  Every worker response is stamped
when authority first observes it on the native-frame clock.  Responses first
observed at or before the latest useful handoff are admitted; later responses are
kept only as latency telemetry and can never enter ranking for that root.

This layer also exposes per-worker arrival age, deadline slack, compute time, and
exact-frame-step counters through V23 timeline metadata.  The policy below the
admission gate remains V32: full quorum before the deadline, partial closure at
the deadline, then action-lineage + proof-lease + reward ranking.
"""

from __future__ import annotations

from fami_pixel.games.smb1.collect_deadline import DeadlineCollectResponseCache

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v26 as v26
import mesen_smb_checkpoint_planner_v28 as v28
import mesen_smb_checkpoint_planner_v29 as v29
import mesen_smb_checkpoint_planner_v32 as v32


PLANNER_NAME = "v33-authority-observed-collect-deadline"
_DEADLINE_FRAMES = max(int(value) for value in v28.COLLECT_HANDOFF_FRAMES)
_DEADLINE_CACHE = DeadlineCollectResponseCache(deadline_frames=_DEADLINE_FRAMES)


def _install_deadline_cache() -> DeadlineCollectResponseCache:
    # Keep one process-local cache so first-observation timestamps survive
    # response-file overwrites and repeated selector calls.
    if v29._COLLECT_RESPONSE_CACHE is not _DEADLINE_CACHE:
        v29._COLLECT_RESPONSE_CACHE = _DEADLINE_CACHE
    return _DEADLINE_CACHE


def _timing_fields(
    *,
    current_frame: int,
    expected_workers: int,
) -> dict:
    meta = dict(v23._latest_forward_meta or {})
    if str(meta.get("objective_mode")) != "COLLECT":
        return {}
    try:
        generation = int(meta["forward_model_generation"])
        root_frame = int(meta["forward_model_source_frame"])
    except (KeyError, TypeError, ValueError):
        return {
            "collect_deadline_admission": "authority-first-seen<=deadline",
            "collect_deadline_frames": int(_DEADLINE_FRAMES),
        }

    timing = _DEADLINE_CACHE.timing_snapshot(
        generation=generation,
        root_frame=root_frame,
        current_frame=int(current_frame),
        expected_workers=int(expected_workers),
    )
    return {
        "collect_deadline_admission": "authority-first-seen<=deadline",
        "collect_cohort_closure_reason": timing["closure_reason"],
        "collect_worker_arrival_age_frames": timing["worker_arrival_age_frames"],
        "collect_worker_deadline_slack_frames": timing["worker_deadline_slack_frames"],
        "collect_worker_compute_ms": timing["worker_compute_ms"],
        "collect_worker_exact_frame_steps": timing["worker_exact_frame_steps"],
        "collect_on_time_workers": timing["on_time_workers"],
        "collect_late_workers": timing["late_workers"],
        "collect_unseen_workers": timing["unseen_workers"],
        "collect_missing_on_time_workers": timing["missing_on_time_workers"],
        "collect_quorum_arrival_age_frames": timing["quorum_arrival_age_frames"],
        "collect_quorum_deadline_margin_frames": timing["quorum_deadline_margin_frames"],
    }


def _best_collect_or_progress(
    response_paths,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Run V32 with irreversible first-observation deadline admission."""

    _install_deadline_cache()
    result = v32._best_collect_or_progress(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )

    timing = _timing_fields(
        current_frame=int(current_frame),
        expected_workers=len(response_paths),
    )
    if timing:
        updated = dict(v23._latest_forward_meta or {})
        updated.update(timing)
        v23._latest_forward_meta = updated
        if result is not None:
            result = dict(result)
            result.update(timing)
    return result


def authority_main(args) -> int:
    _install_deadline_cache().clear()
    v11._log(
        "Planner V33: authority-observed COLLECT deadline enabled | "
        f"deadline={_DEADLINE_FRAMES}f; late workers remain telemetry-only and cannot reopen cohorts"
    )
    return v32.authority_main(args)


def _install_v33_overrides() -> None:
    v32._install_v32_overrides()
    _install_deadline_cache()

    # V26 owns SURVIVE/landing. Replace only its lower COLLECT/PROGRESS delegate;
    # V33 delegates all actual ranking back to V32.
    v26._BASE_V25_PLAN = _best_collect_or_progress

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v33_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
