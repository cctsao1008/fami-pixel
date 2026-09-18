from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys



def _load_v34():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v34.py"
        spec = spec_from_file_location("planner_v34_handoff_planning_seam", path)
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


def test_v34_uses_stable_handoff_policy_primitives():
    v34 = _load_v34()
    source = inspect.getsource(v34._best_collect_or_progress)
    assert "evaluate_handoff_stage(" in source
    assert "collect_anchor_proofs(" in source
    assert "read_available_responses(" in source
    assert "active_workers.issubset(workers)" not in source


def test_v34_handoff_contract_is_explicitly_earliest_to_latest():
    v34 = _load_v34()
    assert tuple(v34.COLLECT_HANDOFF_FRAMES) == (4, 8, 12)
    assert inspect.getsource(v34).count("def _proofs_for_handoff") == 0
    assert inspect.getsource(v34).count("def _anchor_proofs") == 0
