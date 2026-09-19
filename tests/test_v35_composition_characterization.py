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


def test_v35_installer_preserves_v28_authority_plan_wrapper_over_v26_survive_owner():
    v35 = _load_v35()
    v35._install_v35_overrides()
    v28 = v35.v34.v28

    assert v35.v23._best_forward_plan is v28._best_v28_plan
    assert v28._BASE_V26_PLAN is v35.v26._best_v26_plan
    wrapper_source = inspect.getsource(v28._best_v28_plan)
    assert "result = _BASE_V26_PLAN(" in wrapper_source
    assert "_remember_authority_plan(result)" in wrapper_source
    assert type(v28._AUTHORITY_PLAN_MEMORY).__module__ == "fami_pixel.control.authority_plan"

    assert v35.v26._LOWER_PLAN_DELEGATE.selector is v35._best_collect_or_progress
    assert v35.v26._lower_plan_delegate_explicit is True


def test_v35_async_collect_dependencies_are_explicitly_composed():
    v35 = _load_v35()
    control = v35._EAGER_COLLECT_CONTROL

    assert type(control).__module__ == "fami_pixel.control.composition"
    assert control.cache_provider is v35.v34._install_handoff_cache
    assert control.handoff_frames == tuple(v35.v34.COLLECT_HANDOFF_FRAMES)
    assert control.active_workers is v35.v34._active_collect_workers
    assert control.proof_selector is v35.v34.select_lineage_collect_proof
    assert control.ledger is v35.v34.v27._AUTHORITY_ACTION_LEDGER
    assert control.commit_frames == v35.v23.EXECUTION_PREFIX_FRAMES
    assert control.proof_horizon_frames is v35._proof_horizon_for_freshness


def test_v35_collect_progress_dependencies_are_explicitly_composed():
    v35 = _load_v35()
    control = v35._COLLECT_PROGRESS_CONTROL

    assert type(control).__module__ == "fami_pixel.control.composition"
    assert control.target_selector is v35.collect_target_from_radar
    assert type(control.target_selector).__module__ == "fami_pixel.planning.collect_target"
    assert control.progress_selector is v35.v34.v27._best_forward_plan_partial_v27
    assert control.eager_collect is v35._EAGER_COLLECT_CONTROL


def test_v35_star_selector_is_sync_current_root_then_stable_collect_progress_control():
    v35 = _load_v35()
    source = inspect.getsource(v35._best_collect_or_progress)

    assert "_COLLECT_PROGRESS_CONTROL.target_type(live_radar)" in source
    assert 'target_type == "star"' in source
    assert "_LIVE_AUTHORITY_CORE is not None" in source
    assert "_sync_star_plan(" in source
    assert "_COLLECT_PROGRESS_CONTROL.decide(" in source
    assert "v23._latest_forward_meta = decision.meta" in source
    assert "return decision.plan" in source
    assert "v25._collect_target_from_radar(" not in source
    assert "v34.v27._best_forward_plan_partial_v27(" not in source
    assert "_EAGER_COLLECT_CONTROL.decide(" not in source
    assert "read_available_responses(" not in source
    assert "select_eager_collect_decision(" not in source
    assert "v34._install_handoff_cache(" not in source
    assert "v34._active_collect_workers(" not in source
    assert "v34.v30._proof_horizon(" not in source
    assert "v34.select_lineage_collect_proof" not in source
    assert "v34.v27._AUTHORITY_ACTION_LEDGER" not in source
    assert "return v34._best_collect_or_progress(" not in source


def test_v35_sync_speculation_uses_composed_authority_ledger():
    v35 = _load_v35()
    source = inspect.getsource(v35._sync_star_plan)

    assert "_COLLECT_PROGRESS_CONTROL.eager_collect.ledger" in source
    assert "with ledger.suspend_recording():" in source
    assert "_sync_star_plan_untracked(" in source
    assert "v34.v27._AUTHORITY_ACTION_LEDGER" not in source


def test_v35_authority_wrapper_uses_stable_runtime_scope_reset_setup_bootstrap_and_lineage():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert type(v35._AUTHORITY_RUNTIME_SCOPE).__module__ == "fami_pixel.control.authority_runtime"
    assert type(v35._AUTHORITY_RUN_RESET_PLAN).__module__ == "fami_pixel.control.authority_runtime"
    assert type(v35._AUTHORITY_RUN_SETUP_PLAN).__module__ == "fami_pixel.control.authority_runtime"
    assert type(v35._V23_BOOTSTRAP_SETUP_PLAN).__module__ == "fami_pixel.control.authority_runtime"
    assert v35._AUTHORITY_RUN_SETUP_PLAN.names == ("v30-collect-proof-horizon",)
    assert v35._V23_BOOTSTRAP_SETUP_PLAN.names == (
        "v23-process-job",
        "v23-surrogate-model",
        "v23-runtime-dir",
        "v23-live-stack",
    )
    assert "with _AUTHORITY_RUNTIME_SCOPE.controller_layer(capture_layer):" in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_through("v32-collect-response-cache")' in source
    assert "_AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)" in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v29-collect-response-cache")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v28-authority-plan-memory")' in source
    assert "with installed_checkpoint_request_enricher(enricher):" in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-progress-response-cache")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-authority-action-ledger")' in source
    assert "authority_action_recording_layer(_V27._AUTHORITY_ACTION_LEDGER)" in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v25-live-objective")' in source
    assert "_V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)" in source
    assert source.count("_AUTHORITY_RUNTIME_SCOPE.controller_layer(") == 2
    assert "live_control = _build_live_authority_control()" in source
    assert "return live_control.run(args)" in source
    assert "return _V17.authority_main(args)" not in source
    assert "return v23.authority_main(args)" not in source
    assert "return v26._BASE_V25_AUTHORITY(args)" not in source
    assert "return v26.authority_main(args)" not in source
    assert "return _V27.authority_main(args)" not in source
    assert "return _V28.authority_main(args)" not in source
    assert v35._V17 is v35.v23.v17
    assert "_LIVE_AUTHORITY_CORE = None" in source
    assert "base.set_nes_controller_state = capture_authority_core" not in source
    assert "base.set_nes_controller_state = original_set_controller" not in source
