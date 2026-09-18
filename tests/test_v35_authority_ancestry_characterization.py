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

    expected_delegates = (
        (v35.authority_main, "return v34.authority_main(args)"),
        (v34.authority_main, "return v33.authority_main(args)"),
        (v33.authority_main, "return v32.authority_main(args)"),
        (v32.authority_main, "return v31.v30.authority_main(args)"),
        (v30.authority_main, "return v29.authority_main(args)"),
        (v29.authority_main, "return v28.authority_main(args)"),
        (v28.authority_main, "return v27.authority_main(args)"),
        (v27.authority_main, "return v26.authority_main(args)"),
        (v26.authority_main, "return _BASE_V25_AUTHORITY(args)"),
    )
    for authority, expected in expected_delegates:
        assert expected in inspect.getsource(authority)

    # V26 captured the V25 authority function before later installers mutate
    # v23.authority_main, so the bottom of the active wrapper chain is explicit.
    assert v26._BASE_V25_AUTHORITY is v25.authority_main


def test_authority_wrappers_preserve_their_current_stateful_responsibilities():
    v35 = _load_v35()
    v34, v33, v32, _v30, v29, v28, v27, v26, _v25 = _authority_modules(v35)

    v35_source = inspect.getsource(v35.authority_main)
    assert "with _AUTHORITY_RUNTIME_SCOPE.controller_layer(capture_layer):" in v35_source
    assert "return v34.authority_main(args)" in v35_source
    assert "_LIVE_AUTHORITY_CORE = None" in v35_source
    assert "base.set_nes_controller_state = capture_authority_core" not in v35_source
    assert type(v35._AUTHORITY_RUNTIME_SCOPE).__module__ == "fami_pixel.control.authority_runtime"

    assert "_install_handoff_cache().clear()" in inspect.getsource(v34.authority_main)
    assert "_install_deadline_cache().clear()" in inspect.getsource(v33.authority_main)
    assert "v29._COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v32.authority_main)
    assert "_COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v29.authority_main)

    v28_source = inspect.getsource(v28.authority_main)
    assert "_AUTHORITY_PLAN_MEMORY.clear()" in v28_source
    assert "with installed_checkpoint_request_enricher(enricher):" in v28_source

    v27_source = inspect.getsource(v27.authority_main)
    assert "_PROGRESS_RESPONSE_CACHE.clear()" in v27_source
    assert "_AUTHORITY_ACTION_LEDGER.clear()" in v27_source
    assert "with _AUTHORITY_RUNTIME_SCOPE.controller_layer(" in v27_source
    assert "authority_action_recording_layer(_AUTHORITY_ACTION_LEDGER)" in v27_source
    assert "base.set_nes_controller_state = recording_set_controller" not in v27_source
    assert "base.set_nes_controller_state = original_set_controller" not in v27_source
    assert type(v27._AUTHORITY_RUNTIME_SCOPE).__module__ == "fami_pixel.control.authority_runtime"

    assert "_reset_gap_commitment()" in inspect.getsource(v26.authority_main)


def test_authority_extraction_boundary_is_runtime_orchestration_not_policy_rewrite():
    v35 = _load_v35()
    v34, _v33, _v32, _v30, _v29, _v28, v27, v26, _v25 = _authority_modules(v35)

    # Current decision policy has already moved behind stable composition seams,
    # but the live authority runtime still enters through historical wrappers.
    assert "_COLLECT_PROGRESS_CONTROL.decide(" in inspect.getsource(v35._best_collect_or_progress)
    assert "return v34.authority_main(args)" in inspect.getsource(v35.authority_main)

    # Both controller instrumentation layers now use the stable runtime scope.
    # V27 still enters later in the historical ancestry, so runtime calls remain
    # lineage recorder -> V35 live-core capture -> base setter.
    assert "capture_authority_core" in inspect.getsource(v35.authority_main)
    assert "controller_layer(capture_layer)" in inspect.getsource(v35.authority_main)
    assert "authority_action_recording_layer" in inspect.getsource(v27.authority_main)
    assert "_AUTHORITY_RUNTIME_SCOPE.controller_layer" in inspect.getsource(v27.authority_main)

    # V26 remains the safety owner at the bottom of the wrapper chain; extracting
    # runtime ancestry must not move SURVIVE below COLLECT/PROGRESS.
    assert "_reset_gap_commitment()" in inspect.getsource(v26.authority_main)
    assert v35.v26._LOWER_PLAN_DELEGATE.selector is not None
    assert v34.authority_main is not v35.authority_main
