from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_gap_guard_probe_keeps_negative_control_and_rearm_escape():
    tools = (Path(__file__).resolve().parents[1] / "tools").resolve()
    sys.path.insert(0, str(tools))
    try:
        path = tools / "smb1_gap_guard_probe.py"
        spec = spec_from_file_location("fami_pixel_gap_guard_probe_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)

        assert module.LEGACY_GAP_EXTEND_PLAN.name == "legacy_gap_extend4"
        assert module.LEGACY_GAP_EXTEND_PLAN.prefix_frames == 4
        assert module.GAP_ESCAPE_PLAN.name == "gap_escape_rearm"
        assert module.GAP_ESCAPE_PLAN.prefix_frames == 16
        assert module.base.PLANS[0] is module.LEGACY_GAP_EXTEND_PLAN
        assert module.base.PLANS[1] is module.GAP_ESCAPE_PLAN
    finally:
        try:
            sys.path.remove(str(tools))
        except ValueError:
            pass
