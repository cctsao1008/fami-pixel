from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
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


def test_v27_install_replaces_only_progress_search_wiring():
    v27 = _load_v27()

    old_baseline = v27.v25._baseline_payload
    old_partial = v27.v24._best_forward_plan_partial
    old_name = v27.v23.PLANNER_NAME
    old_label = v27.v23._forward_schedule_label
    old_authority = v27.v23.authority_main
    old_file = v27.v23.__file__
    try:
        v27._install_v27_overrides()
        assert v27.v25._baseline_payload is v27._search_baseline_payload
        assert v27.v24._best_forward_plan_partial is v27._best_forward_plan_partial_v27
        assert v27.v23.PLANNER_NAME == v27.PLANNER_NAME
        assert v27.v23._forward_schedule_label is v27._v27_schedule_label
        # COLLECT worker ownership remains V25; V27 only replaces its PROGRESS
        # baseline payload path.
        assert v27.v23.shadow_worker_main is v27.v25.shadow_worker_main
    finally:
        v27.v25._baseline_payload = old_baseline
        v27.v24._best_forward_plan_partial = old_partial
        v27.v23.PLANNER_NAME = old_name
        v27.v23._forward_schedule_label = old_label
        v27.v23.authority_main = old_authority
        v27.v23.__file__ = old_file
