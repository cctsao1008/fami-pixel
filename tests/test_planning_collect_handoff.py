from fami_pixel.planning.collect_handoff import (
    active_worker_ids,
    collect_anchor_proofs,
    collect_handoff_proofs,
    evaluate_handoff_stage,
    ordered_handoffs,
)


def _proof(worker: int, handoff: int, *, anchor: bool = False) -> dict:
    return {
        "worker": worker,
        "collect_handoff_frames": handoff,
        "collect_continuation_anchor": anchor,
        "candidate": f"w{worker}-h{handoff}",
    }


def test_handoff_proofs_filter_stage_and_anchor_and_copy_payloads():
    source = _proof(0, 4)
    cohort = [
        {"worker": 0, "branch_proofs": [source, _proof(0, 8), _proof(0, 4, anchor=True)]},
        {"worker": 1, "branch_proofs": [{"collect_handoff_frames": 4}]},
        {"worker": 2, "branch_proofs": ["bad", None]},
    ]
    proofs, workers = collect_handoff_proofs(cohort, 4)
    assert workers == {0, 1}
    assert [proof["worker"] for proof in proofs] == [0, 1]
    proofs[0]["candidate"] = "changed"
    assert source["candidate"] == "w0-h4"


def test_anchor_proofs_are_separate_from_reward_stages():
    cohort = [
        {
            "worker": 0,
            "branch_proofs": [
                _proof(0, 4),
                _proof(0, 24, anchor=True),
            ],
        }
    ]
    anchors = collect_anchor_proofs(cohort)
    assert len(anchors) == 1
    assert anchors[0]["collect_continuation_anchor"] is True


def test_active_workers_follow_current_shard_ownership():
    active = active_worker_ids(4, has_work=lambda worker, total: worker < 2 and total == 4)
    assert active == {0, 1}
    assert active_worker_ids(0, has_work=lambda worker, total: worker == 0 and total == 1) == {0}


def test_open_incomplete_stage_blocks_later_handoffs():
    cohort = [{"worker": 0, "branch_proofs": [_proof(0, 4)]}]
    stage = evaluate_handoff_stage(
        cohort,
        handoff_frames=4,
        age_frames=3,
        active_workers={0, 1},
    )
    assert stage.complete is False
    assert stage.closed is False
    assert stage.waiting is True
    assert stage.rankable is False


def test_deadline_closes_partial_stage_and_makes_existing_proofs_rankable():
    cohort = [{"worker": 0, "branch_proofs": [_proof(0, 4)]}]
    stage = evaluate_handoff_stage(
        cohort,
        handoff_frames=4,
        age_frames=4,
        active_workers={0, 1},
    )
    assert stage.complete is False
    assert stage.closed is True
    assert stage.waiting is False
    assert stage.rankable is True


def test_empty_closed_stage_is_exhausted_not_rankable():
    stage = evaluate_handoff_stage(
        [],
        handoff_frames=4,
        age_frames=4,
        active_workers={0},
    )
    assert stage.closed is True
    assert stage.waiting is False
    assert stage.rankable is False


def test_handoff_order_is_explicit_and_must_be_strictly_increasing():
    assert ordered_handoffs((4, 8, 12)) == (4, 8, 12)

    for invalid in ((), (0, 4), (4, 4), (8, 4)):
        try:
            ordered_handoffs(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid handoff sequence: {invalid}")
