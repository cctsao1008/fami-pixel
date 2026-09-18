from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys
from types import SimpleNamespace


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_v30_authority_characterization", path)
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


def test_v30_authority_runtime_responsibility_is_proof_horizon_setup_log_and_delegate():
    v35 = _load_v35()
    v30 = v35._V30
    source = inspect.getsource(v30.authority_main)

    assert "proof_horizon = _proof_horizon(args)" in source
    assert "v28.COLLECT_PROOF_HORIZON = int(proof_horizon)" in source
    assert "budget = CollectTreeBudget(" in source
    assert '"Planner V30: shared-prefix delayed COLLECT enabled | "' in source
    assert "return v29.authority_main(args)" in source

    assert ".clear()" not in source
    assert "controller_layer(" not in source
    assert "set_nes_controller_state" not in source


def test_v30_proof_horizon_is_installed_before_v29_authority_delegate(monkeypatch):
    v35 = _load_v35()
    v30 = v35._V30
    calls = []

    monkeypatch.setattr(v30, "_proof_horizon", lambda _args: 37)
    monkeypatch.setattr(v30.v11, "_log", lambda message: calls.append(("log", message)))

    def delegated(_args):
        calls.append(("delegate", int(v30.v28.COLLECT_PROOF_HORIZON)))
        return 19

    monkeypatch.setattr(v30.v29, "authority_main", delegated)

    assert v30.authority_main(SimpleNamespace()) == 19
    assert int(v30.v28.COLLECT_PROOF_HORIZON) == 37
    assert calls[0][0] == "log"
    assert "proof-horizon=37f" in calls[0][1]
    assert calls[1] == ("delegate", 37)


def test_current_v35_keeps_v30_setup_before_transferred_v29_v28_v27_v26_runtime():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert type(v35._AUTHORITY_RUN_SETUP_PLAN).__module__ == "fami_pixel.control.authority_runtime"
    assert v35._AUTHORITY_RUN_SETUP_PLAN.names == ("v30-collect-proof-horizon",)
    assert "_AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)" in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v29-collect-response-cache")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v28-authority-plan-memory")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-progress-response-cache")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-authority-action-ledger")' in source
    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")' in source
    assert "return v26._BASE_V25_AUTHORITY(args)" in source
    assert "return v26.authority_main(args)" not in source
    assert "return _V27.authority_main(args)" not in source
    assert "return _V28.authority_main(args)" not in source
    assert "return _V29.authority_main(args)" not in source
    assert "return _V30.authority_main(args)" not in source

    historical = inspect.getsource(v35._V30.authority_main)
    assert "v28.COLLECT_PROOF_HORIZON = int(proof_horizon)" in historical
    assert "return v29.authority_main(args)" in historical


def test_v35_stable_v30_setup_runs_before_transferred_lower_runtime(monkeypatch, tmp_path):
    v35 = _load_v35()
    calls = []

    monkeypatch.setattr(v35._V30, "_proof_horizon", lambda _args: 41)
    monkeypatch.setattr(v35.v11, "_log", lambda message: calls.append(("log", message)))

    class ResetPlan:
        def reset_through(self, _name):
            calls.append(("reset-prefix", int(v35._V28.COLLECT_PROOF_HORIZON)))

        def reset_named(self, name):
            calls.append((name, int(v35._V28.COLLECT_PROOF_HORIZON)))

    monkeypatch.setattr(v35, "_AUTHORITY_RUN_RESET_PLAN", ResetPlan())

    def delegated(_args):
        calls.append(("delegate", int(v35._V28.COLLECT_PROOF_HORIZON)))
        return 29

    monkeypatch.setattr(v35.v26, "_BASE_V25_AUTHORITY", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
        plan_freshness=16,
    )
    assert v35.authority_main(args) == 29
    assert int(v35._V28.COLLECT_PROOF_HORIZON) == 41

    v30_log_index = next(
        i for i, item in enumerate(calls)
        if item[0] == "log" and "Planner V30:" in item[1]
    )
    v29_reset_index = calls.index(("v29-collect-response-cache", 41))
    v28_reset_index = calls.index(("v28-authority-plan-memory", 41))
    v27_progress_index = calls.index(("v27-progress-response-cache", 41))
    v27_ledger_index = calls.index(("v27-authority-action-ledger", 41))
    v26_gap_index = calls.index(("v26-gap-commitment", 41))
    delegate_index = calls.index(("delegate", 41))
    assert (
        v30_log_index
        < v29_reset_index
        < v28_reset_index
        < v27_progress_index
        < v27_ledger_index
        < v26_gap_index
        < delegate_index
    )
