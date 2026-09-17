from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "extract_smb1_multi_enemy_scenario.py"
_SPEC = spec_from_file_location("extract_smb1_multi_enemy_scenario_test", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
try:
    _SPEC.loader.exec_module(_MOD)
finally:
    sys.modules.pop(_SPEC.name, None)


def _record(
    generation: int,
    *,
    landing_count: int,
    guard: str,
    cluster_count: int,
    cluster_start: int,
    cluster_end: int,
    corridor_start: int = 96,
    corridor_end: int = 160,
) -> dict:
    return {
        "generation": generation,
        "guard_mode": guard,
        "radar": {
            "landing_enemy_count": landing_count,
            "nearest_cluster_count": cluster_count,
            "nearest_cluster_start_dx": cluster_start,
            "nearest_cluster_end_dx": cluster_end,
            "landing_corridor_start_dx": corridor_start,
            "landing_corridor_end_dx": corridor_end,
        },
    }


def test_landing_enemy_count_prefers_top_level_then_radar() -> None:
    assert _MOD.landing_enemy_count({"landing_enemy_count": 3, "radar": {"landing_enemy_count": 1}}) == 3
    assert _MOD.landing_enemy_count({"radar": {"landing_enemy_count": 2}}) == 2
    assert _MOD.landing_enemy_count({"radar": {"landing_enemy_count": None}}) == 0


def test_active_landing_guard_requires_landing_zone_guard_mode() -> None:
    assert _MOD.is_active_landing_guard({"guard_mode": "landing-zone-extend[landing:2]"})
    assert _MOD.is_active_landing_guard({"guard_mode": "landing-zone-escape[landing:2]"})
    assert not _MOD.is_active_landing_guard({"guard_mode": "forward-model-partial[landed]"})
    assert not _MOD.is_active_landing_guard({})


def test_clean_projected_cluster_rejects_current_contact_transient() -> None:
    contact = _record(
        10,
        landing_count=2,
        guard="landing-zone-extend[landing:2]",
        cluster_count=1,
        cluster_start=0,
        cluster_end=0,
    )
    clean = _record(
        11,
        landing_count=2,
        guard="landing-zone-extend[landing:2]",
        cluster_count=2,
        cluster_start=98,
        cluster_end=136,
    )

    assert not _MOD.is_clean_projected_multi_enemy_cluster(contact, 2)
    assert _MOD.is_clean_projected_multi_enemy_cluster(clean, 2)


def test_select_multi_enemy_record_skips_contact_root_and_uses_clean_cluster() -> None:
    records = [
        _record(
            10,
            landing_count=2,
            guard="landing-zone-extend[landing:2]",
            cluster_count=1,
            cluster_start=0,
            cluster_end=0,
        ),
        _record(
            11,
            landing_count=2,
            guard="forward-model-partial[landed]",
            cluster_count=2,
            cluster_start=98,
            cluster_end=136,
        ),
        _record(
            12,
            landing_count=2,
            guard="landing-zone-extend[landing:2]",
            cluster_count=2,
            cluster_start=98,
            cluster_end=136,
        ),
    ]

    selected = _MOD.select_multi_enemy_record(records, 2)
    assert selected["generation"] == 12


def test_projected_cluster_must_fit_inside_configured_corridor() -> None:
    too_early = _record(
        10,
        landing_count=2,
        guard="landing-zone-extend[landing:2]",
        cluster_count=2,
        cluster_start=90,
        cluster_end=130,
    )
    too_late = _record(
        11,
        landing_count=2,
        guard="landing-zone-extend[landing:2]",
        cluster_count=2,
        cluster_start=120,
        cluster_end=170,
    )
    exact = _record(
        12,
        landing_count=2,
        guard="landing-zone-extend[landing:2]",
        cluster_count=2,
        cluster_start=96,
        cluster_end=160,
    )

    assert not _MOD.is_clean_projected_multi_enemy_cluster(too_early, 2)
    assert not _MOD.is_clean_projected_multi_enemy_cluster(too_late, 2)
    assert _MOD.is_clean_projected_multi_enemy_cluster(exact, 2)


def test_selector_can_explicitly_allow_noncluster_root_for_diagnostics() -> None:
    records = [
        _record(
            11,
            landing_count=2,
            guard="landing-zone-extend[landing:2]",
            cluster_count=1,
            cluster_start=0,
            cluster_end=0,
        )
    ]

    selected = _MOD.select_multi_enemy_record(
        records,
        2,
        require_projected_cluster=False,
    )
    assert selected["generation"] == 11


def test_select_multi_enemy_record_fails_without_clean_active_cluster() -> None:
    records = [
        _record(
            10,
            landing_count=2,
            guard="landing-zone-extend[landing:2]",
            cluster_count=1,
            cluster_start=0,
            cluster_end=0,
        ),
        _record(
            11,
            landing_count=2,
            guard="forward-model-partial",
            cluster_count=2,
            cluster_start=98,
            cluster_end=136,
        ),
    ]

    try:
        _MOD.select_multi_enemy_record(records, 2)
    except SystemExit as exc:
        text = str(exc)
        assert "landing_enemy_count >= 2" in text
        assert "active landing-zone guard" in text
        assert "nearest multi-enemy cluster" in text
    else:
        raise AssertionError("expected selector to fail without a clean active multi-enemy cluster")
