import inspect
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v34():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v34.py"
        spec = spec_from_file_location("planner_v34_collect_status_wiring", path)
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


def test_v34_routes_collect_status_through_stable_control_shapers():
    v34 = _load_v34()
    source = inspect.getsource(v34._best_collect_or_progress)

    for shaper in (
        "selected_eager_collect_meta(",
        "selected_collect_anchor_meta(",
        "waiting_eager_handoff_meta(",
        "waiting_lineage_collect_meta(",
        "waiting_collect_cohort_meta(",
    ):
        assert shaper in source

    for inline_status in (
        '"forward_model_status": "selected-eager-handoff-collect-proof"',
        '"forward_model_status": "selected-collect-continuation-anchor"',
        '"forward_model_status": "waiting-eager-collect-handoff"',
        '"forward_model_status": "waiting-lineage-valid-eager-collect-proof"',
        '"forward_model_status": "waiting-eager-collect-cohort"',
    ):
        assert inline_status not in source
