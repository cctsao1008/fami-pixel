from fami_pixel.games.smb1.collect_deadline import DeadlineCollectResponseCache


def _response(*, worker: int, generation=20, root=200, target="star", compute_ms=1.0):
    return {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": target,
        "candidate": f"worker-{worker}",
        "compute_ms": compute_ms,
        "collect_exact_frame_steps": 10 + worker,
    }


def _ingest(cache, responses, *, frame, last=19, retention=24):
    cache.ingest(
        responses,
        current_frame=frame,
        last_applied_generation=last,
        retention_frames=retention,
        target_type="star",
    )


def test_worker_first_seen_after_deadline_is_rejected_permanently():
    cache = DeadlineCollectResponseCache(deadline_frames=12)

    _ingest(cache, [_response(worker=0)], frame=204)
    _ingest(cache, [_response(worker=0), _response(worker=1)], frame=208)

    groups = cache.groups()
    assert cache.worker_set(groups[(20, 200)]) == {0, 1}

    # Worker 2 is first observed at source age 16.  It must never retroactively
    # enter generation 20 even if the response file remains visible afterward.
    _ingest(
        cache,
        [_response(worker=0), _response(worker=1), _response(worker=2, compute_ms=99.0)],
        frame=216,
    )
    _ingest(
        cache,
        [_response(worker=0), _response(worker=1), _response(worker=2, compute_ms=99.0)],
        frame=220,
    )

    groups = cache.groups()
    assert cache.worker_set(groups[(20, 200)]) == {0, 1}
    timing = cache.timing_snapshot(
        generation=20,
        root_frame=200,
        current_frame=220,
        expected_workers=3,
    )
    assert timing["closure_reason"] == "deadline"
    assert timing["worker_arrival_age_frames"] == {"0": 4, "1": 8, "2": 16}
    assert timing["late_workers"] == [2]
    assert timing["missing_on_time_workers"] == [2]
    assert timing["worker_deadline_slack_frames"]["2"] == -4


def test_worker_first_seen_at_deadline_is_on_time():
    cache = DeadlineCollectResponseCache(deadline_frames=12)

    _ingest(cache, [_response(worker=0)], frame=204)
    _ingest(cache, [_response(worker=0), _response(worker=1)], frame=208)
    _ingest(
        cache,
        [_response(worker=0), _response(worker=1), _response(worker=2)],
        frame=212,
    )

    cohort = cache.groups()[(20, 200)]
    assert cache.complete(cohort, expected_workers=3)
    timing = cache.timing_snapshot(
        generation=20,
        root_frame=200,
        current_frame=216,
        expected_workers=3,
    )
    assert timing["closure_reason"] == "quorum"
    assert timing["quorum_arrival_age_frames"] == 12
    assert timing["quorum_deadline_margin_frames"] == 0
    assert timing["late_workers"] == []


def test_timing_state_is_purged_when_generation_is_consumed():
    cache = DeadlineCollectResponseCache(deadline_frames=12)
    _ingest(cache, [_response(worker=0)], frame=204)

    _ingest(cache, [_response(worker=0)], frame=208, last=20)

    assert cache.groups() == {}
    timing = cache.timing_snapshot(
        generation=20,
        root_frame=200,
        current_frame=208,
        expected_workers=3,
    )
    assert timing["worker_arrival_age_frames"] == {}
    assert timing["unseen_workers"] == [0, 1, 2]
