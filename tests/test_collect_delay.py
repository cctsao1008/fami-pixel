from fami_pixel.games.smb1.action_lineage import AuthorityActionLedger
from fami_pixel.games.smb1.collect_delay import (
    compose_delayed_collect_schedule,
    continuation_anchor_schedule,
    select_lineage_collect_proof,
)


RIGHT_B = 0x82
LEFT_B = 0x42
NOOP = 0x00


def _ledger(root: int, buttons: list[int]) -> AuthorityActionLedger:
    ledger = AuthorityActionLedger(max_entries=128)
    for offset, value in enumerate(buttons):
        ledger.record(root + offset, value)
    return ledger


def _proof(
    *,
    candidate: str,
    schedule: list[dict],
    root: int = 200,
    generation: int = 20,
    frames: int = 24,
    reward_key=(1, 1, 0, -10),
    collected: bool = False,
    handoff: int = 8,
):
    return {
        "generation": generation,
        "worker": 0,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": candidate,
        "schedule": schedule,
        "reward_prefix_safe": True,
        "reward_collected": collected,
        "reward_key": list(reward_key),
        "trajectory_frames": frames,
        "collect_handoff_frames": handoff,
    }


def test_delayed_collect_before_handoff_remains_reachable():
    root = 200
    continuation = [{"buttons": RIGHT_B, "frames": 24}]
    reward = [{"buttons": LEFT_B, "frames": 4}, {"buttons": NOOP, "frames": 1}]
    delay8 = compose_delayed_collect_schedule(
        continuation,
        reward,
        handoff_frames=8,
    )
    delay12 = compose_delayed_collect_schedule(
        continuation,
        reward,
        handoff_frames=12,
    )

    # At age 6 both delayed branches still share the actually executed current
    # plan. Reward score may choose the stronger future branch without rebasing.
    selection = select_lineage_collect_proof(
        [
            _proof(candidate="collect_delay8_a", schedule=delay8, reward_key=(1, 1, 0, -5), handoff=8),
            _proof(candidate="collect_delay12_b", schedule=delay12, reward_key=(1, 1, 0, -9), handoff=12),
        ],
        ledger=_ledger(root, [RIGHT_B] * 6),
        current_frame=root + 6,
        last_applied_generation=19,
        target_type="star",
        commit_frames=4,
        retention_frames=24,
    )
    assert selection.proof is not None
    assert selection.proof["candidate"] == "collect_delay8_a"
    assert selection.proof["root_frame"] == root
    assert selection.proof["trajectory_source_age_frames"] == 6


def test_delayed_collect_rejects_branch_after_unexecuted_handoff():
    root = 200
    continuation = [{"buttons": RIGHT_B, "frames": 24}]
    reward = [{"buttons": LEFT_B, "frames": 4}, {"buttons": NOOP, "frames": 1}]
    delay8 = compose_delayed_collect_schedule(continuation, reward, handoff_frames=8)
    delay12 = compose_delayed_collect_schedule(continuation, reward, handoff_frames=12)

    # Live authority kept RIGHT+B through age 10. The 8f branch would already
    # have switched LEFT+B and is therefore unreachable; 12f remains exact.
    selection = select_lineage_collect_proof(
        [
            _proof(candidate="collect_delay8_high", schedule=delay8, reward_key=(9,), handoff=8),
            _proof(candidate="collect_delay12_low", schedule=delay12, reward_key=(1,), handoff=12),
        ],
        ledger=_ledger(root, [RIGHT_B] * 10),
        current_frame=root + 10,
        last_applied_generation=19,
        target_type="star",
        commit_frames=4,
        retention_frames=24,
    )
    assert selection.proof is not None
    assert selection.proof["candidate"] == "collect_delay12_low"
    assert selection.rejected.get("lineage-mismatch") == 1


def test_continuation_anchor_survives_when_reward_handoffs_have_diverged():
    root = 200
    continuation = [{"buttons": RIGHT_B, "frames": 24}]
    reward = [{"buttons": LEFT_B, "frames": 4}, {"buttons": NOOP, "frames": 1}]
    delay8 = compose_delayed_collect_schedule(continuation, reward, handoff_frames=8)
    delay12 = compose_delayed_collect_schedule(continuation, reward, handoff_frames=12)
    anchor = continuation_anchor_schedule(continuation, proof_horizon=24)

    selection = select_lineage_collect_proof(
        [
            _proof(candidate="collect_delay8", schedule=delay8, reward_key=(9,), handoff=8),
            _proof(candidate="collect_delay12", schedule=delay12, reward_key=(8,), handoff=12),
            _proof(candidate="collect_continue_authority", schedule=anchor, reward_key=(0,), handoff=24),
        ],
        ledger=_ledger(root, [RIGHT_B] * 16),
        current_frame=root + 16,
        last_applied_generation=19,
        target_type="star",
        commit_frames=4,
        retention_frames=24,
    )
    assert selection.proof is not None
    assert selection.proof["candidate"] == "collect_continue_authority"
    assert selection.proof["proof_remaining_frames"] == 8
    assert selection.rejected.get("lineage-mismatch") == 2


def test_old_v25_four_frame_prefix_expires_at_age_four():
    root = 200
    old_prefix = [{"buttons": RIGHT_B, "frames": 4}]
    selection = select_lineage_collect_proof(
        [_proof(candidate="legacy_v25", schedule=old_prefix, frames=4, handoff=0)],
        ledger=_ledger(root, [RIGHT_B] * 4),
        current_frame=root + 4,
        last_applied_generation=19,
        target_type="star",
        commit_frames=4,
        retention_frames=24,
    )
    assert selection.proof is None
    assert selection.rejected.get("proof-lease-expired") == 1
