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

    # V30 authority_main owns no run reset and no controller instrumentation.
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


def test_current_v35_extracts_v30_setup_and_enters_v29_directly():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert type(v35._AUTHORITY_RUN_SETUP_PLAN).__module__ == "fami_pixel.control.authority_runtime"
    assert v35._AUTHORITY_RUN_SETUP_PLAN.names == ("v30-collect-proof-horizon",)
    assert "_AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)" in source
    assert "return _V29.authority_main(args)" in source
    assert "return _V30.authority_main(args)" not in source

    # Historical V30 remains independently runnable with its original setup and
    # delegation behavior even though current V35 no longer enters through it.
    historical = inspect.getsource(v35._V30.authority_main)
    assert "v28.COLLECT_PROOF_HORIZON = int(proof_horizon)" in historical
    assert "return v29.authority_main(args)" in historical


def test_v35_stable_v30_setup_installs_horizon_before_v29_delegate(monkeypatch, tmp_path):
    v35 = _load_v35()
    calls = []

    monkeypatch.setattr(v35._V30, "_proof_horizon", lambda _args: 41)
    monkeypatch.setattr(v35.v11, "_log", lambda message: calls.append(("log", message)))
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_RESET_PLAN",
        SimpleNamespace(reset_through=lambda _name: None),
    )

    def delegated(_args):
        calls.append(("delegate", int(v35._V28.COLLECT_PROOF_HORIZON)))
        return 29

    monkeypatch.setattr(v35._V29, "authority_main", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
        plan_freshness=16,
    )
    assert v35.authority_main(args) == 29
    assert int(v35._V28.COLLECT_PROOF_HORIZON) == 41
    v30_logs = [message for kind, message in calls if kind == "log" and "Planner V30:" in message]
    assert len(v30_logs) == 1
    assert "proof-horizon=41f" in v30_logs[0]
    assert calls[-1] == ("delegate", 41)
