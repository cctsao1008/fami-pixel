from fami_pixel.planning import select_progress_proof


REQUIRED = {"run", "jump"}


def _anchors(proofs):
    return {
        proof["candidate"]
        for proof in proofs
        if proof.get("candidate") in REQUIRED
    }


def _lineage_all(proofs, *, current_frame):
    valid = []
    for proof in proofs:
        candidate = dict(proof)
        candidate.setdefault("proof_remaining_frames", 8)
        candidate.setdefault("lineage_matched_frames", 4)
        valid.append(candidate)
    return valid, {}


def test_progress_selects_best_score_from_newest_complete_group():
    groups = {
        (4, 100): [
            {
                "candidate": "run",
                "root_frame": 100,
                "score": [1, 2],
                "trajectory_event": "landing",
            },
            {
                "candidate": "jump",
                "root_frame": 100,
                "score": [1, 3],
                "trajectory_event": "landing",
            },
        ],
        (3, 90): [
            {"candidate": "run", "root_frame": 90, "score": [1, 9]},
            {"candidate": "jump", "root_frame": 90, "score": [1, 8]},
        ],
    }

    decision = select_progress_proof(
        groups,
        current_frame=104,
        required_anchors=REQUIRED,
        evaluated_anchors=_anchors,
        lineage_validator=_lineage_all,
        live_radar={"camera_x": 1},
    )

    assert decision.plan is not None
    assert decision.plan["candidate"] == "jump"
    assert decision.plan["age"] == 4
    assert decision.meta["forward_model_generation"] == 4
    assert decision.meta["forward_model_status"] == "selected-lineage-cohort"


def test_progress_incomplete_newest_falls_back_to_older_complete_group():
    groups = {
        (5, 110): [
            {"candidate": "run", "root_frame": 110, "score": [1, 9]},
        ],
        (4, 100): [
            {"candidate": "run", "root_frame": 100, "score": [1, 2]},
            {"candidate": "jump", "root_frame": 100, "score": [1, 3]},
        ],
    }

    decision = select_progress_proof(
        groups,
        current_frame=114,
        required_anchors=REQUIRED,
        evaluated_anchors=_anchors,
        lineage_validator=_lineage_all,
        live_radar={},
    )

    assert decision.plan is not None
    assert decision.plan["cohort_generation"] == 4


def test_progress_complete_rejection_status_beats_partial_when_no_valid_group():
    groups = {
        (6, 120): [
            {"candidate": "run", "root_frame": 120, "score": [1, 1]},
        ],
        (5, 110): [
            {"candidate": "run", "root_frame": 110, "score": [1, 2]},
            {"candidate": "jump", "root_frame": 110, "score": [1, 3]},
        ],
    }

    def reject(_proofs, *, current_frame):
        return [], {"lineage-mismatch": 2}

    decision = select_progress_proof(
        groups,
        current_frame=124,
        required_anchors=REQUIRED,
        evaluated_anchors=_anchors,
        lineage_validator=reject,
        live_radar={},
    )

    assert decision.plan is None
    assert decision.meta["forward_model_status"] == "waiting-lineage-valid-proof"
    assert decision.meta["forward_model_generation"] == 5


def test_progress_empty_groups_wait_for_ranked_cohort():
    decision = select_progress_proof(
        {},
        current_frame=50,
        required_anchors=REQUIRED,
        evaluated_anchors=_anchors,
        lineage_validator=_lineage_all,
        live_radar={},
    )

    assert decision.plan is None
    assert decision.meta == {
        "forward_model_status": "waiting-ranked-cohort",
        "objective_mode": "PROGRESS",
        "search_response_count": 0,
        "search_required_anchors": ["jump", "run"],
        "search_evaluated_anchors": [],
    }
