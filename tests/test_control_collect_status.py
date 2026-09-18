from fami_pixel.control import (
    selected_collect_anchor_meta,
    selected_eager_collect_meta,
    waiting_collect_cohort_meta,
    waiting_eager_handoff_meta,
    waiting_lineage_collect_meta,
)


def test_selected_eager_collect_meta_preserves_v34_contract():
    timing = {"proofs": [{"candidate": "collect_delay4_right"}]}
    result = {
        "candidate": "collect_delay4_right",
        "trajectory_event": "prefix_alive",
        "reward_collected": False,
        "proof_remaining_frames": 5,
    }
    meta = selected_eager_collect_meta(
        target_type="star",
        generation=20,
        root_frame=200,
        age_frames=3,
        result=result,
        handoff_frames=4,
        stage_complete=True,
        stage_closed=False,
        workers={2, 0, 1},
        active_workers={1, 0, 2},
        valid_count=2,
        rejected={"lineage-mismatch": 1},
        handoff_timing=timing,
    )
    assert meta == {
        "forward_model_status": "selected-eager-handoff-collect-proof",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
        "forward_model_generation": 20,
        "forward_model_plan": "collect_delay4_right",
        "forward_model_event": "prefix_alive",
        "forward_model_source_frame": 200,
        "forward_model_source_age_frames": 3,
        "forward_model_reward_collected": False,
        "collect_handoff_frames": 4,
        "collect_handoff_stage_complete": True,
        "collect_handoff_stage_closed": False,
        "collect_handoff_workers": [0, 1, 2],
        "collect_active_workers": [0, 1, 2],
        "collect_lineage_valid_count": 2,
        "collect_lineage_rejected": {"lineage-mismatch": 1},
        "collect_proof_remaining_frames": 5,
        "collect_handoff_timing": timing,
    }


def test_selected_anchor_meta_preserves_v34_contract():
    timing = {"proofs": []}
    meta = selected_collect_anchor_meta(
        target_type="star",
        generation=21,
        root_frame=204,
        age_frames=12,
        result={"candidate": "collect_continue_authority"},
        handoff_timing=timing,
    )
    assert meta == {
        "forward_model_status": "selected-collect-continuation-anchor",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
        "forward_model_generation": 21,
        "forward_model_plan": "collect_continue_authority",
        "forward_model_source_frame": 204,
        "forward_model_source_age_frames": 12,
        "collect_handoff_timing": timing,
    }


def test_waiting_handoff_meta_preserves_v34_contract():
    timing = {"proofs": [{"admitted": True}]}
    meta = waiting_eager_handoff_meta(
        target_type="mushroom",
        generation=22,
        root_frame=300,
        age_frames=2,
        handoff_frames=4,
        workers={1},
        active_workers={0, 1},
        handoff_timing=timing,
    )
    assert meta == {
        "forward_model_status": "waiting-eager-collect-handoff",
        "objective_mode": "COLLECT",
        "collect_target_type": "mushroom",
        "forward_model_generation": 22,
        "forward_model_source_frame": 300,
        "forward_model_source_age_frames": 2,
        "collect_waiting_handoff_frames": 4,
        "collect_handoff_workers": [1],
        "collect_active_workers": [0, 1],
        "collect_handoff_timing": timing,
    }


def test_waiting_lineage_meta_preserves_v34_contract():
    timing = {"proofs": [{"admitted": True}]}
    meta = waiting_lineage_collect_meta(
        target_type="star",
        generation=23,
        root_frame=400,
        age_frames=4,
        handoff_frames=4,
        workers={0, 2},
        rejected={"proof-expired": 2},
        handoff_timing=timing,
    )
    assert meta == {
        "forward_model_status": "waiting-lineage-valid-eager-collect-proof",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
        "forward_model_generation": 23,
        "forward_model_source_frame": 400,
        "forward_model_source_age_frames": 4,
        "collect_handoff_frames": 4,
        "collect_handoff_workers": [0, 2],
        "collect_lineage_rejected": {"proof-expired": 2},
        "collect_handoff_timing": timing,
    }


def test_waiting_cohort_meta_preserves_v34_contract():
    assert waiting_collect_cohort_meta(target_type="star") == {
        "forward_model_status": "waiting-eager-collect-cohort",
        "objective_mode": "COLLECT",
        "collect_target_type": "star",
    }
