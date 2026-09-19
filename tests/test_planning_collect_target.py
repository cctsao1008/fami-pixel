from fami_pixel.planning import COLLECT_TARGET_TYPES, collect_target_from_radar


def test_collect_target_types_match_current_v25_contract():
    assert COLLECT_TARGET_TYPES == frozenset(
        {"mushroom", "fire_flower", "star", "one_up"}
    )


def test_collect_target_prefers_sticky_target_over_nearest_reward():
    assert collect_target_from_radar(
        {
            "collect_target_type": "star",
            "nearest_reward_type": "mushroom",
        }
    ) == "star"


def test_collect_target_uses_nearest_reward_without_sticky_target():
    assert collect_target_from_radar({"nearest_reward_type": "fire_flower"}) == "fire_flower"


def test_collect_target_rejects_unknown_and_preserves_falsey_fallback():
    assert collect_target_from_radar({"collect_target_type": "coin"}) is None
    assert collect_target_from_radar({}) is None
    assert collect_target_from_radar(
        {
            "collect_target_type": "",
            "nearest_reward_type": "one_up",
        }
    ) == "one_up"
