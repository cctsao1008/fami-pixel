import pytest

from fami_pixel.games.smb1.landing import (
    LANDING_ENEMY,
    LANDING_GAP,
    LANDING_SAFE,
    LANDING_UNKNOWN,
    TERRAIN_GAP,
    TERRAIN_SAFE,
    TERRAIN_UNKNOWN,
    assess_landing_zone,
    cluster_forward_enemies,
)


def _radar(*dxs, lookahead_px=0, columns=None):
    return {
        "enemies": [
            {"slot": i, "id": 0x06, "state": 0, "x": 1000 + dx, "y": 176, "dx": dx}
            for i, dx in enumerate(dxs)
        ],
        "lookahead_px": lookahead_px,
        "columns": list(columns or ()),
    }


def _column(dx, *, surface_row=11, validity="CURRENT"):
    return {
        "x": 1000 + dx,
        "dx": dx,
        "surface_row": surface_row,
        "surface_y": None if surface_row is None else 32 + surface_row * 16,
        "validity": validity,
    }


def _safe_corridor_columns():
    return [
        _column(96),
        _column(112),
        _column(128),
        _column(144),
        _column(160),
    ]


def test_single_near_enemy_does_not_create_false_safe_without_terrain_evidence():
    assessment = assess_landing_zone(_radar(42))

    assert assessment.forward_enemy_count == 1
    assert assessment.nearest_cluster is not None
    assert assessment.nearest_cluster.count == 1
    assert assessment.landing_enemy_count == 0
    assert assessment.landing_enemy_clear is True
    assert assessment.terrain_status == TERRAIN_UNKNOWN
    assert assessment.landing_status == LANDING_UNKNOWN
    assert assessment.landing_safe is False
    assert assessment.landing_unknown is True


def test_known_supported_corridor_is_safe_only_with_enemy_clearance():
    assessment = assess_landing_zone(
        _radar(42, lookahead_px=192, columns=_safe_corridor_columns())
    )

    assert assessment.terrain_status == TERRAIN_SAFE
    assert assessment.landing_status == LANDING_SAFE
    assert assessment.landing_safe is True
    assert assessment.landing_known_unsafe is False


def test_multi_enemy_scene_marks_enemies_inside_landing_corridor():
    assessment = assess_landing_zone(
        _radar(42, 65, 126, 150, lookahead_px=192, columns=_safe_corridor_columns())
    )

    assert assessment.forward_enemy_count == 4
    assert len(assessment.clusters) == 2
    assert assessment.clusters[0].enemy_dxs == (42, 65)
    assert assessment.clusters[1].enemy_dxs == (126, 150)
    assert assessment.landing_enemy_dxs == (126, 150)
    assert assessment.landing_enemy_count == 2
    assert assessment.landing_enemy_unsafe is True
    assert assessment.landing_status == LANDING_ENEMY
    assert assessment.landing_unsafe is True
    assert assessment.landing_safe is False


def test_known_empty_current_column_makes_landing_terrain_gap():
    columns = _safe_corridor_columns()
    columns[2] = _column(128, surface_row=None)
    assessment = assess_landing_zone(_radar(lookahead_px=192, columns=columns))

    assert assessment.terrain_status == TERRAIN_GAP
    assert assessment.terrain_gap_dxs == (128,)
    assert assessment.landing_status == LANDING_GAP
    assert assessment.landing_safe is False
    assert assessment.landing_known_unsafe is True
    # Legacy enemy-policy signal is intentionally not repurposed as pit policy.
    assert assessment.landing_unsafe is False


def test_unknown_column_prevents_false_safe_even_when_bytes_look_supported():
    columns = _safe_corridor_columns()
    columns[-1] = _column(160, surface_row=11, validity="UNKNOWN")
    assessment = assess_landing_zone(_radar(lookahead_px=176, columns=columns))

    assert assessment.terrain_status == TERRAIN_UNKNOWN
    assert assessment.terrain_unknown_dxs == (160,)
    assert assessment.landing_status == LANDING_UNKNOWN
    assert assessment.landing_safe is False
    assert assessment.landing_unknown is True


def test_short_terrain_coverage_is_unknown_not_safe():
    assessment = assess_landing_zone(
        _radar(
            lookahead_px=32,
            columns=[_column(16), _column(32)],
        )
    )

    assert assessment.terrain_status == TERRAIN_UNKNOWN
    assert assessment.terrain_sample_count == 0
    assert assessment.landing_status == LANDING_UNKNOWN
    assert assessment.landing_safe is False


def test_cluster_gap_is_explicit_and_tunable():
    clusters = cluster_forward_enemies(_radar(40, 70, 125), cluster_gap_px=60)

    assert len(clusters) == 1
    assert clusters[0].count == 3
    assert clusters[0].start_dx == 40
    assert clusters[0].end_dx == 125


def test_behind_enemies_are_not_part_of_forward_landing_assessment():
    assessment = assess_landing_zone(_radar(-12, 110))

    assert assessment.forward_enemy_count == 1
    assert assessment.landing_enemy_dxs == (110,)


def test_landing_corridor_validation():
    with pytest.raises(ValueError):
        assess_landing_zone(_radar(100), landing_near_px=120, landing_far_px=80)
