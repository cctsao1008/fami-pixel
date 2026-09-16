from fami_pixel.games.smb1.action_lineage import (
    AuthorityActionLedger,
    schedule_buttons_at,
    validate_branch_proof,
)


def _proof(*, root=200, frames=49, schedule=None):
    return {
        "root_frame": root,
        "trajectory_frames": frames,
        "trajectory_safe_resolved": True,
        "schedule": schedule
        or [
            {"buttons": 0x42, "frames": 4},
            {"buttons": 0x00, "frames": 2},
            {"buttons": 0x82, "frames": 1},
            {"buttons": 0x83, "frames": 15},
            {"buttons": 0x82, "frames": 1},
        ],
    }


def _record_matching(ledger, proof, *, current_frame, skip_frame=None):
    root = int(proof["root_frame"])
    for frame in range(root, current_frame):
        if frame == skip_frame:
            continue
        age = frame - root
        buttons = schedule_buttons_at(proof["schedule"], age)
        assert buttons is not None
        ledger.record(frame, buttons)


def test_exact_lineage_match_preserves_stale_proof_phase():
    ledger = AuthorityActionLedger()
    proof = _proof()
    _record_matching(ledger, proof, current_frame=208)

    result = validate_branch_proof(
        proof,
        ledger=ledger,
        current_frame=208,
        commit_frames=4,
    )

    assert result.valid is True
    assert result.reason == "lineage-match"
    assert result.source_age == 8
    assert result.matched_frames == 8
    assert result.proof_remaining_frames == 41
    assert schedule_buttons_at(proof["schedule"], result.source_age) == 0x83


def test_one_frame_lineage_mismatch_rejects_exact_proof():
    ledger = AuthorityActionLedger()
    proof = _proof()
    _record_matching(ledger, proof, current_frame=208)
    ledger.record(204, 0x82)  # candidate expected NOOP at this transition

    result = validate_branch_proof(
        proof,
        ledger=ledger,
        current_frame=208,
        commit_frames=4,
    )

    assert result.valid is False
    assert result.reason == "lineage-mismatch"
    assert result.mismatch_frame == 204
    assert result.expected_buttons == 0x00
    assert result.actual_buttons == 0x82


def test_proof_lease_must_cover_source_age_plus_next_commit():
    ledger = AuthorityActionLedger()
    proof = _proof(frames=10)
    _record_matching(ledger, proof, current_frame=208)

    result = validate_branch_proof(
        proof,
        ledger=ledger,
        current_frame=208,
        commit_frames=4,
    )

    assert result.valid is False
    assert result.reason == "proof-lease-expired"
    assert result.source_age == 8
    assert result.proof_remaining_frames == 2


def test_missing_authority_history_fails_closed():
    ledger = AuthorityActionLedger()
    proof = _proof()
    _record_matching(ledger, proof, current_frame=208, skip_frame=205)

    result = validate_branch_proof(
        proof,
        ledger=ledger,
        current_frame=208,
        commit_frames=4,
    )

    assert result.valid is False
    assert result.reason == "authority-ledger-gap"
