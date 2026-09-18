from dataclasses import dataclass

from fami_pixel.planning.collect_selection import (
    select_collect_proof,
    shape_eager_collect_result,
)


@dataclass(frozen=True)
class _Selection:
    proof: dict | None
    valid_count: int = 0
    rejected: dict[str, int] | None = None


def test_select_collect_proof_detaches_payloads_and_preserves_authority_arguments():
    source = {"candidate": "collect_delay4_left", "root_frame": 100}
    captured = {}

    def selector(proofs, **kwargs):
        captured["proofs"] = proofs
        captured.update(kwargs)
        proofs[0]["candidate"] = "mutated-inside-selector"
        return _Selection(proof=dict(proofs[0]), valid_count=1, rejected={})

    ledger = object()
    selection = select_collect_proof(
        [source],
        selector=selector,
        ledger=ledger,
        current_frame=103,
        last_applied_generation=7,
        target_type="star",
        commit_frames=4,
        retention_frames=16,
    )

    assert selection.valid_count == 1
    assert source["candidate"] == "collect_delay4_left"
    assert captured["ledger"] is ledger
    assert captured["current_frame"] == 103
    assert captured["last_applied_generation"] == 7
    assert captured["target_type"] == "star"
    assert captured["commit_frames"] == 4
    assert captured["retention_frames"] == 16


def test_shape_eager_collect_result_preserves_selected_proof_and_live_phase():
    proof = {
        "root_frame": 200,
        "candidate": "collect_delay4_right",
        "proof_remaining_frames": 9,
        "schedule": [{"buttons": 0x82, "frames": 4}],
    }
    selection = _Selection(proof=proof, valid_count=1, rejected={})

    result = shape_eager_collect_result(
        selection,
        current_frame=203,
        live_radar={"collect_target_type": "star", "nearest_enemy_dx": 80},
        handoff_frames=4,
    )

    assert result["trajectory_root_frame"] == 200
    assert result["trajectory_source_age_frames"] == 3
    assert result["root_frame"] == 200
    assert result["age"] == 3
    assert result["guard_mode"] == (
        "collect-eager-handoff[4f,collect_delay4_right,src-age:3f,lease:9f]"
    )
    assert result["live_radar"] == {
        "collect_target_type": "star",
        "nearest_enemy_dx": 80,
    }
    assert proof == {
        "root_frame": 200,
        "candidate": "collect_delay4_right",
        "proof_remaining_frames": 9,
        "schedule": [{"buttons": 0x82, "frames": 4}],
    }


def test_shape_eager_collect_result_copies_live_radar():
    radar = {"collect_target_type": "mushroom"}
    selection = _Selection(
        proof={"root_frame": 10, "candidate": "collect_delay8_coast"},
        valid_count=1,
        rejected={},
    )
    result = shape_eager_collect_result(
        selection,
        current_frame=14,
        live_radar=radar,
        handoff_frames=8,
    )
    radar["collect_target_type"] = "star"
    assert result["live_radar"]["collect_target_type"] == "mushroom"
    assert result["guard_mode"].endswith("lease:Nonef]")


def test_shape_eager_collect_result_rejects_empty_selection():
    selection = _Selection(proof=None, valid_count=0, rejected={"lineage-mismatch": 1})
    try:
        shape_eager_collect_result(
            selection,
            current_frame=20,
            live_radar={},
            handoff_frames=4,
        )
    except ValueError as exc:
        assert "empty COLLECT selection" in str(exc)
    else:
        raise AssertionError("expected empty selection to be rejected")
