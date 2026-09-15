from fami_pixel.adapters.mesen import NES_A, NES_B, NES_RIGHT
from fami_pixel.games.smb1.terrain_guard import gap_escape_schedule, near_gap_guard


def test_near_airborne_gap_requires_rearm_commitment():
    # V25 field regression around generation 302: gap=9, Mario airborne.
    guard = near_gap_guard({"nearest_gap_dx": 9, "grounded": False}, trigger_px=80)
    assert guard is not None
    assert guard.gap_dx == 9
    assert guard.mode == "airborne-rearm-commit"
    assert guard.grounded is False


def test_near_grounded_gap_delegates_to_rearm_path():
    guard = near_gap_guard({"nearest_gap_dx": 39, "grounded": True}, trigger_px=80)
    assert guard is not None
    assert guard.mode == "grounded-rearm"


def test_absent_or_far_gap_is_not_promoted_to_safe():
    assert near_gap_guard({"nearest_gap_dx": None, "grounded": False}, trigger_px=80) is None
    assert near_gap_guard({"nearest_gap_dx": 81, "grounded": False}, trigger_px=80) is None


def test_gap_escape_matches_long_rearm_jump_then_run_tail():
    # Exact Mesen from pit-v25-gen302: the old RIGHT+A+B 4f extension died;
    # RIGHT+B 1f -> RIGHT+A+B 15f -> RIGHT+B landed safely.
    schedule = gap_escape_schedule(hold_frames=15)
    assert schedule == [
        {"buttons": NES_RIGHT | NES_B, "frames": 1},
        {"buttons": NES_RIGHT | NES_A | NES_B, "frames": 15},
        {"buttons": NES_RIGHT | NES_B, "frames": 1},
    ]
