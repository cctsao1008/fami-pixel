from fami_pixel.games.smb1.landing import (
    LANDING_UNKNOWN,
    TERRAIN_UNKNOWN,
    assess_landing_zone,
)


def test_x2587_short_terrain_window_is_unknown_not_false_safe():
    """Encode the field shape that originally reported landing_safe=true at a pit.

    The historical V20 terminal evidence around X≈2587 had only 32 px of terrain
    lookahead, no positive nearest-gap result, and an apparent raised/occupied
    collision-buffer column about 21 px ahead. Enemy occupancy was clear, which
    the old landing helper incorrectly promoted to full SAFE.

    With a +96..+160 projected landing corridor, 32 px of validated coverage is
    insufficient by construction. The correct semantic result is UNKNOWN.
    """

    radar = {
        "player_x": 2587,
        "lookahead_px": 32,
        "screen_right_x": 2608,
        "terrain_valid_through_x": 2608,
        "nearest_gap_dx": None,
        "nearest_obstacle_dx": 21,
        "enemies": [],
        "columns": [
            {
                "x": 2608,
                "dx": 21,
                "surface_row": 9,
                "surface_y": 176,
                "validity": "CURRENT",
                "surface_address": 0x05E3,
                "surface_value": 0x54,
                "collision_samples": [],
            }
        ],
    }

    assessment = assess_landing_zone(radar)
    payload = assessment.to_payload()

    assert assessment.terrain_status == TERRAIN_UNKNOWN
    assert assessment.landing_status == LANDING_UNKNOWN
    assert assessment.landing_safe is False
    assert assessment.landing_unknown is True
    assert payload["landing_safe"] is False
    assert payload["landing_status"] == "UNKNOWN"
