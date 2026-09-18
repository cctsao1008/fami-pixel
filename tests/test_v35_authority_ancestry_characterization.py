from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_authority_ancestry", path)
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


def _authority_modules(v35):
    v34 = v35.v34
    v33 = v34.v33
    v32 = v33.v32
    v30 = v32.v31.v30
    v29 = v30.v29
    v28 = v29.v28
    v27 = v28.v27
    v26 = v27.v26
    v25 = v26.v25
    return v34, v33, v32, v30, v29, v28, v27, v26, v25


def test_current_authority_runtime_delegation_chain_is_exact():
    v35 = _load_v35()
    v34, v33, v32, v30, v29, v28, v27, v26, v25 = _authority_modules(v35)

    # Current V35 now owns the historical V34..V27 runtime responsibilities and
    # enters the remaining historical authority ancestry at V26.
    expected_current_delegates = (
        (v35.authority_main, "return v26.authority_main(args)"),
        (v26.authority_main, "return _BASE_V25_AUTHORITY(args)"),
    )
    for authority, expected in expected_current_delegates:
        assert expected in inspect.getsource(authority)

    # Historical examples remain independently runnable with their original
    # standalone delegation/setup/reset ownership.
    assert "return v33.authority_main(args)" in inspect.getsource(v34.authority_main)
    assert "return v32.authority_main(args)" in inspect.getsource(v33.authority_main)
    assert "return v31.v30.authority_main(args)" in inspect.getsource(v32.authority_main)
    assert "return v29.authority_main(args)" in inspect.getsource(v30.authority_main)
    assert "return v28.authority_main(args)" in inspect.getsource(v29.authority_main)
    assert "return v27.authority_main(args)" in inspect.getsource(v28.authority_main)
    assert "return v26.authority_main(args)" in inspect.getsource(v27.authority_main)

    assert v26._BASE_V25_AUTHORITY is v25.authority_main


def test_authority_wrappers_preserve_their_current_stateful_responsibilities():
    v35 = _load_v35()
    v34, v33, v32, v30, v29, v28, v27, v26, _v25 = _authority_modules(v35)

    v35_source = inspect.getsource(v35.authority_main)
    assert "with _AUTHORITY_RUNTIME_SCOPE.controller_layer(capture_layer):" in v35_source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_through("v32-collect-response-cache")' in v35_source
    assert "_AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)" in v35_source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v29-collect-response-cache")' in v35_source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v28-authority-plan-memory")' in v35_source
    assert 'with installed_checkpoint_request_enricher(enricher):' in v35_source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-progress-response-cache")' in v35_source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-authority-action-ledger")' in v35_source
    assert "authority_action_recording_layer(_V27._AUTHORITY_ACTION_LEDGER)" in v35_source
    assert "return v26.authority_main(args)" in v35_source
    assert "return _V27.authority_main(args)" not in v35_source
    assert type(v35._AUTHORITY_RUNTIME_SCOPE).__module__ == "fami_pixel.control.authority_runtime"
    assert type(v35._AUTHORITY_RUN_SETUP_PLAN).__module__ == "fami_pixel.control.authority_runtime"

    # Historical standalone wrappers keep provenance behavior.
    assert "_install_handoff_cache().clear()" in inspect.getsource(v34.authority_main)
    assert "_install_deadline_cache().clear()" in inspect.getsource(v33.authority_main)
    assert "v29._COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v32.authority_main)
    assert "v28.COLLECT_PROOF_HORIZON = int(proof_horizon)" in inspect.getsource(v30.authority_main)
    assert "_COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v29.authority_main)

    assert v35._AUTHORITY_RUN_RESET_PLAN.names[2:4] == (
        "v32-collect-response-cache",
        "v29-collect-response-cache",
    )

    v28_source = inspect.getsource(v28.authority_main)
    assert "_AUTHORITY_PLAN_MEMORY.clear()" in v28_source
    assert "with installed_checkpoint_request_enricher(enricher):" in v28_source

    v27_source = inspect.getsource(v27.authority_main)
    assert "_PROGRESS_RESPONSE_CACHE.clear()" in v27_source
    assert "_AUTHORITY_ACTION_LEDGER.clear()" in v27_source
    assert "with _AUTHORITY_RUNTIME_SCOPE.controller_layer(" in v27_source
    assert "authority_action_recording_layer(_AUTHORITY_ACTION_LEDGER)" in v27_source
    assert type(v27._AUTHORITY_RUNTIME_SCOPE).__module__ == "fami_pixel.control.authority_runtime"

    assert "_reset_gap_commitment()" in inspect.getsource(v26.authority_main)


def test_authority_extraction_boundary_is_runtime_orchestration_not_policy_rewrite():
    v35 = _load_v35()
    v34, _v33, _v32, _v30, _v29, _v28, v27, v26, _v25 = _authority_modules(v35)

    assert "_COLLECT_PROGRESS_CONTROL.decide(" in inspect.getsource(v35._best_collect_or_progress)
    assert "return v26.authority_main(args)" in inspect.getsource(v35.authority_main)

    # Current controller invocation order remains lineage recorder -> live-core
    # capture -> base even though the V27 wrapper itself is now bypassed.
    source = inspect.getsource(v35.authority_main)
    assert "capture_authority_core" in source
    assert "controller_layer(capture_layer)" in source
    assert "authority_action_recording_layer(_V27._AUTHORITY_ACTION_LEDGER)" in source
    assert source.count("_AUTHORITY_RUNTIME_SCOPE.controller_layer(") == 2

    # Historical V27 remains independently runnable with the same stable recorder.
    assert "authority_action_recording_layer" in inspect.getsource(v27.authority_main)

    # V26 remains the safety owner; runtime extraction must not move SURVIVE.
    assert "_reset_gap_commitment()" in inspect.getsource(v26.authority_main)
    assert v35.v26._LOWER_PLAN_DELEGATE.selector is not None
    assert v34.authority_main is not v35.authority_main
