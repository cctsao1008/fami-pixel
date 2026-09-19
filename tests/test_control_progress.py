from fami_pixel.control.progress import (
    ProgressResponseCache,
    evaluated_anchor_set,
    lineage_valid_proofs,
    response_branch_proofs,
)
from fami_pixel.games.smb1.action_lineage import AuthorityActionLedger


def test_response_branch_proofs_inherits_parent_identity_fields():
    response = {
        "generation": 7,
        "worker": 2,
        "root_frame": 100,
        "planner_mode": "progress-search",
        "branch_proofs": [
            {"candidate": "run"},
            {"candidate": "jump", "worker": 3},
        ],
    }

    proofs = response_branch_proofs(response)

    assert proofs == [
        {
            "candidate": "run",
            "generation": 7,
            "worker": 2,
            "root_frame": 100,
            "planner_mode": "progress-search",
        },
        {
            "candidate": "jump",
            "worker": 3,
            "generation": 7,
            "root_frame": 100,
            "planner_mode": "progress-search",
        },
    ]


def test_progress_response_cache_ingests_groups_and_evicts_historical_entries():
    cache = ProgressResponseCache()
    cache.ingest(
        [
            {
                "planner_mode": "progress-search",
                "generation": 5,
                "worker": 0,
                "root_frame": 100,
                "candidate": "run",
            },
            {
                "planner_mode": "collect-search",
                "generation": 6,
                "root_frame": 110,
                "candidate": "ignored",
            },
            {
                "planner_mode": "progress-search",
                "generation": 6,
                "worker": 1,
                "root_frame": 110,
                "branch_proofs": [
                    {"candidate": "run"},
                    {"candidate": "jump"},
                ],
            },
        ],
        current_frame=114,
        freshness=8,
        last_applied_generation=5,
        live_horizon_frames=64,
    )

    groups = cache.groups()
    assert set(groups) == {(6, 110)}
    assert {proof["candidate"] for proof in groups[(6, 110)]} == {"run", "jump"}

    # Future roots are removed rather than accepted as a negative-age proof.
    cache.ingest(
        [
            {
                "planner_mode": "progress-search",
                "generation": 7,
                "root_frame": 200,
                "candidate": "future",
            }
        ],
        current_frame=114,
        freshness=8,
        last_applied_generation=5,
        live_horizon_frames=64,
    )
    assert (7, 200) not in cache.groups()


def test_evaluated_anchor_set_counts_direct_and_reported_anchor_coverage():
    required = {"fm_long_jump", "fm_brake_jump"}
    proofs = [
        {"candidate": "fm_long_jump"},
        {
            "candidate": "beam_other",
            "search_evaluated_anchors": ["fm_brake_jump", "not-required"],
        },
    ]

    assert evaluated_anchor_set(proofs, required_anchors=required) == required


def test_lineage_valid_proofs_shapes_matches_and_rejection_counts():
    ledger = AuthorityActionLedger(max_entries=16)
    ledger.record(100, 0x80)
    ledger.record(101, 0x80)

    valid = {
        "candidate": "run",
        "root_frame": 100,
        "trajectory_frames": 8,
        "trajectory_safe_resolved": True,
        "schedule": [{"buttons": 0x80, "frames": 8}],
    }
    mismatch = {
        "candidate": "jump",
        "root_frame": 100,
        "trajectory_frames": 8,
        "trajectory_safe_resolved": True,
        "schedule": [{"buttons": 0x01, "frames": 8}],
    }

    accepted, rejected = lineage_valid_proofs(
        [valid, mismatch],
        ledger=ledger,
        current_frame=102,
        commit_frames=4,
    )

    assert len(accepted) == 1
    assert accepted[0]["candidate"] == "run"
    assert accepted[0]["trajectory_source_age_frames"] == 2
    assert accepted[0]["lineage_matched_frames"] == 2
    assert accepted[0]["proof_remaining_frames"] == 6
    assert accepted[0]["lineage_validation"] == "lineage-match"
    assert rejected == {"lineage-mismatch": 1}
