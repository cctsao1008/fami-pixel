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
        spec = spec_from_file_location("planner_v35_v26_authority_characterization", path)
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


def test_v26_historical_authority_responsibility_is_gap_reset_log_and_delegate():
    v35 = _load_v35()
    source = inspect.getsource(v35.v26.authority_main)

    assert "_reset_gap_commitment()" in source
    assert '"Planner V26: current scene SURVIVE guards enabled | "' in source
    assert "return _BASE_V25_AUTHORITY(args)" in source


def test_current_v35_extracts_v26_runtime_but_preserves_v26_survive_policy_owner():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")'
    v25_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v25-live-objective")'
    bootstrap = "_V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)"
    delegate = "return _V17.authority_main(args)"

    assert reset in source
    assert v25_reset in source
    assert bootstrap in source
    assert '"Planner V26: current scene SURVIVE guards enabled | "' in source
    assert delegate in source
    assert source.index(reset) < source.index(v25_reset) < source.index(bootstrap) < source.index(delegate)
    assert "return v26.authority_main(args)" not in source
    assert "return v26._BASE_V25_AUTHORITY(args)" not in source
    assert "return v23.authority_main(args)" not in source
    assert v35.v26._BASE_V25_AUTHORITY is v35.v25.authority_main

    # Authority-wrapper extraction must not move the planner policy itself.
    v35._install_v35_overrides()
    assert v35.v34.v28._BASE_V26_PLAN is v35.v26._best_v26_plan
    assert v35.v23._best_forward_plan is v35.v34.v28._best_v28_plan
    assert v35.v26._LOWER_PLAN_DELEGATE.selector is v35._best_collect_or_progress


def test_v35_v26_gap_reset_still_precedes_v25_reset_bootstrap_and_v17(monkeypatch, tmp_path):
    v35 = _load_v35()
    calls = []

    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: None),
    )
    monkeypatch.setattr(
        v35,
        "_V23_BOOTSTRAP_SETUP_PLAN",
        SimpleNamespace(
            setup_run_state=lambda _args: calls.append("v23-bootstrap")
            or {"v23-runtime-dir": Path("test-runtime")}
        ),
    )

    class ResetPlan:
        def reset_through(self, _name):
            pass

        def reset_named(self, name):
            calls.append(name)

    monkeypatch.setattr(v35, "_AUTHORITY_RUN_RESET_PLAN", ResetPlan())

    def forbidden(_args):
        raise AssertionError("historical V26 authority wrapper was invoked")

    monkeypatch.setattr(v35.v26, "authority_main", forbidden)

    def delegated(_args):
        calls.append("v17")
        return 17

    monkeypatch.setattr(v35._V17, "authority_main", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 17
    assert calls[-4:] == [
        "v26-gap-commitment",
        "v25-live-objective",
        "v23-bootstrap",
        "v17",
    ]
