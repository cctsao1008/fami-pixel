from dataclasses import dataclass, field

from fami_pixel.control import select_eager_collect_decision


@dataclass
class _Selection:
    proof: dict | None
    valid_count: int = 0
    rejected: dict[str, int] = field(default_factory=dict)


class _Cache:
    def __init__(self, groups):
        self.groups = list(groups)
        self.ingest_calls = []

    def ingest(self, responses, **kwargs):
        self.ingest_calls.append(([dict(item) for item in responses], dict(kwargs)))

    def ordered_groups(self):
        return list(self.groups)

    def handoff_snapshot(self, *, generation, root_frame):
        return {"generation": generation, "root_frame": root_frame, "proofs": []}


def _proof(*, worker=0, handoff=4, anchor=False):
    return {
        "generation": 20,
        "worker": worker,
        "root_frame": 200,
        "candidate": "collect_continue_authority" if anchor else f"collect_delay{handoff}_right",
        "trajectory_event": "prefix_alive",
        "reward_collected": False,
        "proof_remaining_frames": 5,
        "collect_handoff_frames": handoff,
        "collect_continuation_anchor": anchor,
        "schedule": [{"buttons": 0x82, "frames": 4}],
    }


def _selector(proofs, **_kwargs):
    proofs = list(proofs)
    if not proofs:
        return _Selection(None, rejected={"empty": 1})
    return _Selection(dict(proofs[0]), valid_count=len(proofs))


def test_selects_earliest_complete_handoff_and_shapes_status():
    proof = _proof(worker=0, handoff=4)
    cohort = [{"worker": 0, "branch_proofs": [proof]}]
    cache = _Cache([((20, 200), cohort)])

    decision = select_eager_collect_decision(
        [{"worker": 0}],
        cache=cache,
        handoff_frames=(4, 8, 12),
        current_frame=203,
        last_applied_generation=19,
        target_type="star",
        live_radar={"collect_target_type": "star"},
        retention_frames=16,
        active_workers={0},
        proof_selector=_selector,
        ledger=object(),
        commit_frames=4,
    )

    assert decision.plan is not None
    assert decision.plan["candidate"] == "collect_delay4_right"
    assert decision.plan["root_frame"] == 200
    assert decision.plan["age"] == 3
    assert decision.meta["forward_model_status"] == "selected-eager-handoff-collect-proof"
    assert decision.meta["collect_handoff_frames"] == 4
    assert cache.ingest_calls[0][1]["target_type"] == "star"


def test_open_incomplete_earliest_handoff_blocks_later_stages():
    proof = _proof(worker=0, handoff=4)
    cohort = [{"worker": 0, "branch_proofs": [proof]}]
    cache = _Cache([((20, 200), cohort)])

    decision = select_eager_collect_decision(
        [],
        cache=cache,
        handoff_frames=(4, 8),
        current_frame=202,
        last_applied_generation=19,
        target_type="star",
        live_radar={},
        retention_frames=16,
        active_workers={0, 1},
        proof_selector=_selector,
        ledger=object(),
        commit_frames=4,
    )

    assert decision.plan is None
    assert decision.meta["forward_model_status"] == "waiting-eager-collect-handoff"
    assert decision.meta["collect_waiting_handoff_frames"] == 4
    assert decision.meta["collect_handoff_workers"] == [0]
    assert decision.meta["collect_active_workers"] == [0, 1]


def test_continuation_anchor_is_considered_only_after_reward_handoffs_exhausted():
    anchor = _proof(worker=0, handoff=12, anchor=True)
    cohort = [{"worker": 0, "branch_proofs": [anchor]}]
    cache = _Cache([((20, 200), cohort)])

    decision = select_eager_collect_decision(
        [],
        cache=cache,
        handoff_frames=(4,),
        current_frame=204,
        last_applied_generation=19,
        target_type="star",
        live_radar={"collect_target_type": "star"},
        retention_frames=16,
        active_workers={0},
        proof_selector=_selector,
        ledger=object(),
        commit_frames=4,
    )

    assert decision.plan is not None
    assert decision.plan["candidate"] == "collect_continue_authority"
    assert decision.meta["forward_model_status"] == "selected-collect-continuation-anchor"


def test_rankable_but_rejected_stage_reports_lineage_wait_status():
    proof = _proof(worker=0, handoff=4)
    cohort = [{"worker": 0, "branch_proofs": [proof]}]
    cache = _Cache([((20, 200), cohort)])

    def reject(_proofs, **_kwargs):
        return _Selection(None, rejected={"proof-expired": 1})

    decision = select_eager_collect_decision(
        [],
        cache=cache,
        handoff_frames=(4,),
        current_frame=203,
        last_applied_generation=19,
        target_type="star",
        live_radar={},
        retention_frames=16,
        active_workers={0},
        proof_selector=reject,
        ledger=object(),
        commit_frames=4,
    )

    assert decision.plan is None
    assert decision.meta["forward_model_status"] == "waiting-lineage-valid-eager-collect-proof"
    assert decision.meta["collect_lineage_rejected"] == {"proof-expired": 1}


def test_empty_cache_reports_waiting_cohort():
    cache = _Cache([])
    decision = select_eager_collect_decision(
        [],
        cache=cache,
        handoff_frames=(4, 8, 12),
        current_frame=203,
        last_applied_generation=19,
        target_type="star",
        live_radar={},
        retention_frames=16,
        active_workers={0},
        proof_selector=_selector,
        ledger=object(),
        commit_frames=4,
    )

    assert decision.plan is None
    assert decision.meta == {
        "forward_model_status": "waiting-eager-collect-cohort",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
    }
