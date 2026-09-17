from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_composition_characterization", path)
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


def test_current_installer_chain_is_v35_through_v27_before_v24_v25_v26_base():
    v35 = _load_v35()
    v34 = v35.v34
    v33 = v34.v33
    v32 = v33.v32
    v31 = v32.v31
    v30 = v31.v30
    v29 = v30.v29
    v28 = v29.v28
    v27 = v28.v27

    chain = (
        (v35._install_v35_overrides, "v34._install_v34_overrides()"),
        (v34._install_v34_overrides, "v33._install_v33_overrides()"),
        (v33._install_v33_overrides, "v32._install_v32_overrides()"),
        (v32._install_v32_overrides, "v31._install_v31_overrides()"),
        (v31._install_v31_overrides, "v30._install_v30_overrides()"),
        (v30._install_v30_overrides, "v29._install_v29_overrides()"),
        (v29._install_v29_overrides, "v28._install_v28_overrides()"),
        (v28._install_v28_overrides, "v27._install_v27_overrides()"),
    )
    for installer, expected in chain:
        assert expected in inspect.getsource(installer)

    v27_source = inspect.getsource(v27._install_v27_overrides)
    assert "v25.v24._install_v24_overrides()" in v27_source
    assert "v25._install_v25_overrides()" in v27_source
    assert "v26._install_v26_overrides()" in v27_source


def test_v35_installs_lower_selector_through_stable_control_seam_after_v34_stack():
    v35 = _load_v35()
    source = inspect.getsource(v35._install_v35_overrides)

    assert source.index("v34._install_v34_overrides()") < source.index(
        "v26.install_lower_plan_delegate(_best_collect_or_progress)"
    )
    assert "v26._BASE_V25_PLAN = _best_collect_or_progress" not in source
    assert "v23.PLANNER_NAME = PLANNER_NAME" in source
    assert "v23.authority_main = authority_main" in source
    assert "v23.__file__ = __file__" in source


def test_v35_installer_leaves_v26_survive_owner_and_explicitly_sets_lower_delegate():
    v35 = _load_v35()
    v35._install_v35_overrides()

    assert v35.v23._best_forward_plan is v35.v26._best_v26_plan
    assert v35.v26._LOWER_PLAN_DELEGATE.selector is v35._best_collect_or_progress
    assert v35.v26._lower_plan_delegate_explicit is True


def test_v35_star_selector_is_sync_current_root_then_v34_fallback():
    v35 = _load_v35()
    source = inspect.getsource(v35._best_collect_or_progress)

    assert 'target_type == "star"' in source
    assert "_LIVE_AUTHORITY_CORE is not None" in source
    assert "_sync_star_plan(" in source
    assert "return v34._best_collect_or_progress(" in source


def test_v35_sync_speculation_is_excluded_from_authority_action_lineage():
    v35 = _load_v35()
    source = inspect.getsource(v35._sync_star_plan)

    assert "_AUTHORITY_ACTION_LEDGER" in source
    assert "with ledger.suspend_recording():" in source
    assert "_sync_star_plan_untracked(" in source


def test_v35_authority_wrapper_delegates_to_v34_and_restores_controller_setter():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert "original_set_controller = base.set_nes_controller_state" in source
    assert "base.set_nes_controller_state = capture_authority_core" in source
    assert "return v34.authority_main(args)" in source
    assert "finally:" in source
    assert "base.set_nes_controller_state = original_set_controller" in source
