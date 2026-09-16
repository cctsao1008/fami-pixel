from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys


def _load_v27():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v27.py"
        spec = spec_from_file_location("planner_v27_bounded_search_smoke", path)
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


def test_v27_declares_bounded_rank_prune_contract():
    v27 = _load_v27()

    assert v27.PLANNER_NAME == "v27-bounded-beam-rank-prune"
    assert v27.SEARCH_DEPTH == 3
    assert v27.SEARCH_TOP_K == 12
    assert "BEAM coast2 -> hold_right_jump4" in v27._v27_schedule_label(
        "beam_coast2__hold_right_jump4"
    )


def test_v27_wiring_keeps_collect_worker_and_replaces_progress_payload():
    v27 = _load_v27()
    source = inspect.getsource(v27._install_v27_overrides)

    assert "v25._baseline_payload = _search_baseline_payload" in source
    assert "v24._best_forward_plan_partial = _best_forward_plan_partial_v27" in source
    assert "v23._forward_schedule_label = _v27_schedule_label" in source
    assert "v23.shadow_worker_main" not in source


def test_v27_async_contract_keeps_branch_proofs_and_records_authority_actions():
    v27 = _load_v27()

    payload_source = inspect.getsource(v27._search_baseline_payload)
    selector_source = inspect.getsource(v27._best_forward_plan_partial_v27)
    authority_source = inspect.getsource(v27.authority_main)

    assert 'payload["branch_proofs"] = branch_proofs' in payload_source
    assert "validate_branch_proof" in inspect.getsource(v27._lineage_valid_proofs)
    assert 'result["root_frame"] = source_root_frame' in selector_source
    assert 'result["age"] = source_age' in selector_source
    assert "_AUTHORITY_ACTION_LEDGER.record" in authority_source
