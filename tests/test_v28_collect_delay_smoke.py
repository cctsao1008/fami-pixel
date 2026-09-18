from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys


def _load_v28():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v28.py"
        spec = spec_from_file_location("planner_v28_collect_delay", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        return module
    finally:
        try:
            sys.path.remove(str(examples))
        except ValueError:
            pass


def test_v28_declares_delay_compensated_collect_contract():
    v28 = _load_v28()
    assert v28.PLANNER_NAME == "v28-delay-compensated-collect"
    assert tuple(v28.COLLECT_HANDOFF_FRAMES) == (8, 12)
    assert v28.COLLECT_PROOF_HORIZON == 24
    assert v28.CONTINUATION_CANDIDATE == "collect_continue_authority"


def test_v28_projects_current_plan_from_real_phase_through_stable_memory():
    v28 = _load_v28()
    v28._AUTHORITY_PLAN_MEMORY.clear()
    assert v28._AUTHORITY_PLAN_MEMORY.remember(
        {
            "root_frame": 200,
            "candidate": "fm_brake_jump",
            "schedule": [
                {"buttons": 0x42, "frames": 4},
                {"buttons": 0x00, "frames": 2},
                {"buttons": 0x82, "frames": 1},
                {"buttons": 0x83, "frames": 15},
                {"buttons": 0x82, "frames": 1},
            ],
        }
    )
    continuation = v28._continuation_schedule_for_frame(208)
    assert continuation
    # At source age 8 the exact brake-jump schedule is already in RIGHT+A+B.
    assert continuation[0]["buttons"] == 0x83


def test_v28_remember_authority_plan_uses_stable_control_memory():
    v28 = _load_v28()
    v28._AUTHORITY_PLAN_MEMORY.clear()
    v28._remember_authority_plan(
        {
            "root_frame": 300,
            "candidate": "candidate-a",
            "schedule": [{"buttons": 0x82, "frames": 8}],
        }
    )
    snapshot = v28._AUTHORITY_PLAN_MEMORY.snapshot
    assert snapshot is not None
    assert snapshot["root_frame"] == 300
    assert snapshot["candidate"] == "candidate-a"


def test_v28_installer_replaces_only_lower_collect_progress_delegate_and_worker():
    v28 = _load_v28()
    source = inspect.getsource(v28._install_v28_overrides)
    assert "v27._install_v27_overrides()" in source
    assert "v26._BASE_V25_PLAN = _best_collect_or_progress" in source
    assert "v23._best_forward_plan = _best_v28_plan" in source
    assert "v23.shadow_worker_main = shadow_worker_main" in source
    assert "v23.__file__ = __file__" in source
