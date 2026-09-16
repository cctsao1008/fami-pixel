from fami_pixel.games.smb1.observation import Smb1Observation
from fami_pixel.games.smb1.trajectory_search import (
    DEFAULT_ANCHOR_NAMES,
    build_search_frontier,
    generate_bounded_trajectory_plans,
    shard_ranked_frontier,
    surrogate_record_for_plan,
)


def _observation(*, vx=24, vy=0):
    return Smb1Observation(
        native_frame_id=1000,
        smb_frame_counter=10,
        world=0,
        level=0,
        mario_x_abs=512,
        mario_y=176,
        mario_y_high=1,
        player_state=0,
        player_x_speed=vx & 0xFF,
        player_y_speed=vy & 0xFF,
        raw_joypad=0x82,
        oper_mode=1,
        oper_mode_task=3,
        game_engine_subroutine=8,
    )


class _FakeModel:
    def predict(self, record):
        name = str(record["candidate"]["name"])
        # Intentionally dislike the mandatory anchors and strongly prefer one
        # generated multi-chunk candidate. Anchors still must survive pruning.
        if name == "beam_coast2__hold_right_jump4":
            return {
                "delta_x": 99.0,
                "risk_probability": 0.9,
                "no_progress_probability": 0.9,
            }
        if name in DEFAULT_ANCHOR_NAMES:
            return {
                "delta_x": -50.0,
                "risk_probability": 1.0,
                "no_progress_probability": 1.0,
            }
        return {
            "delta_x": 10.0,
            "risk_probability": 0.2,
            "no_progress_probability": 0.1,
        }


def test_bounded_search_generates_real_multi_chunk_trajectories():
    plans = generate_bounded_trajectory_plans(depth=3)
    names = {plan.name for plan in plans}

    assert "fm_long_jump" in names
    assert "beam_coast2__hold_right_jump4" in names
    assert "beam_brake4__coast2__rearm_short" in names
    assert any(name.count("__") >= 2 for name in names if name.startswith("beam_"))
    assert all(plan.prefix_frames <= 28 for plan in plans)

    signatures = {
        (
            tuple((command.action.value, command.frame_count) for command in plan.commands),
            plan.tail_action.value,
        )
        for plan in plans
    }
    assert len(signatures) == len(plans)


def test_surrogate_record_uses_signed_motion_and_full_candidate_schedule():
    plans = generate_bounded_trajectory_plans(depth=2)
    plan = next(plan for plan in plans if plan.name == "beam_coast2__hold_right_jump4")
    record = surrogate_record_for_plan(_observation(vx=-7, vy=-12), plan)

    assert record["start"]["vx"] == -7
    assert record["start"]["vy"] == -12
    assert record["start"]["joypad"] == 0x82
    assert record["candidate"]["horizon_frames"] == plan.prefix_frames
    assert len(record["candidate"]["schedule"]) == len(plan.commands)


def test_surrogate_prunes_but_cannot_remove_diversity_anchors():
    frontier = build_search_frontier(
        _observation(),
        _FakeModel(),
        depth=2,
        top_k=8,
    )
    selected = {item.plan.name: item for item in frontier.ranked}

    assert frontier.generated > frontier.top_k
    assert frontier.pruned == frontier.generated - 8
    assert len(frontier.ranked) == 8
    assert "beam_coast2__hold_right_jump4" in selected
    for name in DEFAULT_ANCHOR_NAMES:
        assert name in selected
        assert selected[name].anchored is True


def test_ranked_frontier_shards_deterministically_across_workers():
    frontier = build_search_frontier(
        _observation(),
        _FakeModel(),
        depth=2,
        top_k=8,
    )
    shards = [shard_ranked_frontier(frontier, index, 4) for index in range(4)]
    flattened = [item.plan.name for shard in shards for item in shard]

    assert len(flattened) == 8
    assert set(flattened) == {item.plan.name for item in frontier.ranked}
    assert all(len(shard) == 2 for shard in shards)
