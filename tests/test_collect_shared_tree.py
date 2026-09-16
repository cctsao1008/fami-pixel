import pytest

from fami_pixel.games.smb1.collect_shared_tree import (
    CollectTreeBudget,
    minimum_proof_horizon,
)


def test_default_async_collect_horizon_covers_source_age_plus_commit():
    assert minimum_proof_horizon(max_source_age=16, commit_frames=4) == 20


def test_shared_tree_hits_sub_200_exact_step_budget_for_default_collect_vocab():
    budget = CollectTreeBudget(
        chunk_count=8,
        handoffs=(8, 12),
        proof_horizon=20,
    )

    assert budget.reward_branch_count == 16
    assert budget.proof_count == 17
    assert budget.naive_exact_steps == 340
    assert budget.shared_trunk_steps == 12
    assert budget.reward_suffix_steps == 160
    assert budget.anchor_suffix_steps == 8
    assert budget.shared_exact_steps == 180
    assert budget.saved_exact_steps == 160
    assert budget.reduction_ratio == pytest.approx(160 / 340)


def test_shared_tree_preserves_savings_if_longer_24f_proof_is_requested():
    budget = CollectTreeBudget(
        chunk_count=8,
        handoffs=(8, 12),
        proof_horizon=24,
    )
    assert budget.naive_exact_steps == 408
    assert budget.shared_exact_steps == 248
    assert budget.saved_exact_steps == 160


def test_budget_rejects_horizon_that_does_not_extend_past_handoff():
    with pytest.raises(ValueError):
        CollectTreeBudget(chunk_count=8, handoffs=(8, 12), proof_horizon=12)
