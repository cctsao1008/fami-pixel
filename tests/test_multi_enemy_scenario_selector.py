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


def test_select_multi_enemy_record_uses_earliest_semantic_match() -> None:
    records = [
        {"generation": 10, "radar": {"landing_enemy_count": 1}},
        {"generation": 11, "radar": {"landing_enemy_count": 2}},
        {"generation": 12, "radar": {"landing_enemy_count": 3}},
    ]

    selected = _MOD.select_multi_enemy_record(records, 2)
    assert selected["generation"] == 11


def test_select_multi_enemy_record_fails_when_threshold_is_absent() -> None:
    records = [
        {"generation": 10, "radar": {"landing_enemy_count": 1}},
        {"generation": 11, "radar": {"landing_enemy_count": 1}},
    ]

    try:
        _MOD.select_multi_enemy_record(records, 2)
    except SystemExit as exc:
        assert "landing_enemy_count >= 2" in str(exc)
    else:
        raise AssertionError("expected selector to fail without a qualifying multi-enemy record")
