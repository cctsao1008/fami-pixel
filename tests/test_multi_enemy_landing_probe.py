from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "smb1_multi_enemy_landing_probe.py"
_SPEC = spec_from_file_location("smb1_multi_enemy_landing_probe_test", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
try:
    _SPEC.loader.exec_module(_MOD)
finally:
    sys.modules.pop(_SPEC.name, None)


def test_manifest_multi_enemy_count_requires_explicit_fixture_metadata() -> None:
    assert _MOD.manifest_multi_enemy_count({"selection_landing_enemy_count": 2}) == 2
    assert _MOD.manifest_multi_enemy_count({"selection_landing_enemy_count": "3"}) == 3
    assert _MOD.manifest_multi_enemy_count({}) == 0


def test_output_accepts_only_generic_probe_safe_selection_marker() -> None:
    assert _MOD.output_has_safe_selection(
        "long_jump event=landed status=RESOLVED\n"
        "SELECT     : long_jump -> landed key=(3, 0, 92, 92)\n"
        "TrajectoryProbe: DONE\n"
    )
    assert not _MOD.output_has_safe_selection(
        "NO SAFE    : no Mesen-resolved non-death trajectory exists\n"
        "TrajectoryProbe: DONE\n"
    )
    assert not _MOD.output_has_safe_selection(
        "PROVISIONAL: run -> horizon\nTrajectoryProbe: DONE\n"
    )
