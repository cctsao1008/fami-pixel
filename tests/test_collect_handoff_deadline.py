from fami_pixel.games.smb1.collect_handoff_deadline import (
    HandoffDeadlineCollectResponseCache,
)


def _proof(*, worker: int, candidate: str, handoff: int, generation=20, root=200):
    return {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": candidate,
        "collect_handoff_frames": handoff,
        "reward_prefix_safe": True,
        "trajectory_frames": handoff + 4,
        "schedule": [{"buttons": 0x82, "frames": handoff}, {"buttons": 0x42, "frames": 4}],
    }


def _response(worker: int, proofs: list[dict], *, generation=20, root=200):
    payload = {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": proofs[0]["candidate"] if proofs else f"coverage-{worker}",
        "branch_proofs": proofs,
        "compute_ms": 1.0,
    }
    return payload


def _ingest(cache, responses, frame, *, last=19):
    cache.ingest(
        responses,
        current_frame=frame,
        last_applied_generation=last,
        retention_frames=24,
        target_type="star",
    )


def test_plus4_proof_first_seen_after_plus4_is_never_admitted():
    cache = HandoffDeadlineCollectResponseCache(deadline_frames=12)
    late4 = _proof(worker=0, candidate="collect_delay4_left4", handoff=4)
    ontime8 = _proof(worker=0, candidate="collect_delay8_left4", handoff=8)

    _ingest(cache, [_response(0, [late4, ontime8])], 206)

    cohort = cache.groups()[(20, 200)]
    proofs = cohort[0]["branch_proofs"]
    assert [proof["candidate"] for proof in proofs] == ["collect_delay8_left4"]

    snapshot = cache.handoff_snapshot(generation=20, root_frame=200)
    rows = {row["candidate"]: row for row in snapshot["proofs"]}
    assert rows["collect_delay4_left4"]["arrival_age_frames"] == 6
    assert rows["collect_delay4_left4"]["deadline_slack_frames"] == -2
    assert rows["collect_delay4_left4"]["admitted"] is False
    assert rows["collect_delay8_left4"]["admitted"] is True


def test_later_proof_does_not_inherit_workers_early_first_seen_time():
    cache = HandoffDeadlineCollectResponseCache(deadline_frames=12)
    plus4 = _proof(worker=0, candidate="collect_delay4_jump", handoff=4)
    plus8 = _proof(worker=0, candidate="collect_delay8_jump", handoff=8)

    # Worker itself is first seen at age 3 with only the +4 branch.
    _ingest(cache, [_response(0, [plus4])], 203)
    assert cache.proof_arrival_age(
        generation=20, root_frame=200, worker=0, candidate="collect_delay4_jump"
    ) == 3

    # +8 is appended by the same worker only at age 9; it must be late even
    # though that worker has been known since age 3.
    _ingest(cache, [_response(0, [plus4, plus8])], 209)
    cohort = cache.groups()[(20, 200)]
    names = [proof["candidate"] for proof in cohort[0]["branch_proofs"]]
    assert names == ["collect_delay4_jump"]
    assert cache.proof_arrival_age(
        generation=20, root_frame=200, worker=0, candidate="collect_delay8_jump"
    ) == 9


def test_admitted_plus4_proof_remains_cached_but_lineage_decides_reachability_later():
    cache = HandoffDeadlineCollectResponseCache(deadline_frames=12)
    plus4 = _proof(worker=0, candidate="collect_delay4_jump", handoff=4)

    _ingest(cache, [_response(0, [plus4])], 203)
    _ingest(cache, [_response(0, [plus4])], 208)

    cohort = cache.groups()[(20, 200)]
    assert [proof["candidate"] for proof in cohort[0]["branch_proofs"]] == [
        "collect_delay4_jump"
    ]
    # Keeping the proof is intentional. Whether it is still reachable at age 8
    # is an action-lineage question, not a cache/deadline question.
