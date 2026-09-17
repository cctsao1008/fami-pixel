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


def test_landing_enemy_count_prefers_top_level_then_radar() -> None:
    assert _MOD.landing_enemy_count({"landing_enemy_count": 3, "radar": {"landing_enemy_count": 1}}) == 3
    assert _MOD.landing_enemy_count({"radar": {"landing_enemy_count": 2}}) == 2
    assert _MOD.landing_enemy_count({"radar": {"landing_enemy_count": None}}) == 0


def test_active_landing_guard_requires_landing_zone_guard_mode() -> None:
    assert _MOD.is_active_landing_guard({"guard_mode": "landing-zone-extend[landing:2]"})
    assert _MOD.is_active_landing_guard({"guard_mode": "landing-zone-escape[landing:2]"})
    assert not _MOD.is_active_landing_guard({"guard_mode": "forward-model-partial[landed]"})
    assert not _MOD.is_active_landing_guard({})


def test_select_multi_enemy_record_skips_passive_count_only_match() -> None:
    records = [
        {"generation": 10, "radar": {"landing_enemy_count": 1}},
        {"generation": 11, "radar": {"landing_enemy_count": 2}, "guard_mode": "forward-model-partial[landed]"},
        {"generation": 12, "radar": {"landing_enemy_count": 2}, "guard_mode": "landing-zone-extend[landing:2]"},
        {"generation": 13, "radar": {"landing_enemy_count": 3}, "guard_mode": "landing-zone-escape[landing:3]"},
    ]

    selected = _MOD.select_multi_enemy_record(records, 2)
    assert selected["generation"] == 12


def test_selector_can_explicitly_allow_passive_count_only_match() -> None:
    records = [
        {"generation": 11, "radar": {"landing_enemy_count": 2}},
        {"generation": 12, "radar": {"landing_enemy_count": 2}, "guard_mode": "landing-zone-extend[x]"},
    ]

    selected = _MOD.select_multi_enemy_record(records, 2, require_active_guard=False)
    assert selected["generation"] == 11


def test_select_multi_enemy_record_fails_when_active_guard_is_absent() -> None:
    records = [
        {"generation": 10, "radar": {"landing_enemy_count": 1}},
        {"generation": 11, "radar": {"landing_enemy_count": 2}, "guard_mode": "forward-model-partial"},
    ]

    try:
        _MOD.select_multi_enemy_record(records, 2)
    except SystemExit as exc:
        text = str(exc)
        assert "landing_enemy_count >= 2" in text
        assert "active landing-zone guard" in text
    else:
        raise AssertionError("expected selector to fail without an active landing guard")
