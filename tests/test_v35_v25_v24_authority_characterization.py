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
        spec = spec_from_file_location("planner_v35_v25_v24_authority_characterization", path)
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


def test_historical_v25_v24_authority_shells_are_reset_log_delegate_only():
    v35 = _load_v35()

    v25_source = inspect.getsource(v35.v25.authority_main)
    assert "_LIVE_OBJECTIVE.clear()" in v25_source
    assert '"Planner V25: sticky COLLECT objective enabled | "' in v25_source
    assert "return _BASE_V24_AUTHORITY(args)" in v25_source

    v24_source = inspect.getsource(v35._V24.authority_main)
    assert ".clear()" not in v24_source
    assert '"Planner V24: latency-tolerant forward model enabled | "' in v24_source
    assert "return _BASE_AUTHORITY_MAIN(args)" in v24_source

    assert v35.v25._BASE_V24_AUTHORITY is v35._V24.authority_main
    assert v35._V24._BASE_AUTHORITY_MAIN is v35.v23.authority_main


def test_current_v35_transfers_v25_reset_v24_log_then_stable_v23_bootstrap():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    v26_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")'
    v25_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v25-live-objective")'
    v25_log = '"Planner V25: sticky COLLECT objective enabled | "'
    v24_log = '"Planner V24: latency-tolerant forward model enabled | "'
    bootstrap = "bootstrap = _V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)"
    delegate = "return _V17.authority_main(args)"

    for token in (v26_reset, v25_reset, v25_log, v24_log, bootstrap, delegate):
        assert token in source
    assert (
        source.index(v26_reset)
        < source.index(v25_reset)
        < source.index(v25_log)
        < source.index(v24_log)
        < source.index(bootstrap)
        < source.index(delegate)
    )
    assert "return v26._BASE_V25_AUTHORITY(args)" not in source
    assert "return v25._BASE_V24_AUTHORITY(args)" not in source
    assert "return _V24.authority_main(args)" not in source
    assert "return v23.authority_main(args)" not in source


def test_v35_v25_reset_happens_before_v17_without_calling_v25_v24_v23_wrappers(
    monkeypatch,
    tmp_path,
):
    v35 = _load_v35()
    calls = []

    monkeypatch.setattr(v35.v11, "_log", lambda message: calls.append(("log", message)))
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: None),
    )
    monkeypatch.setattr(
        v35,
        "_V23_BOOTSTRAP_SETUP_PLAN",
        SimpleNamespace(
            setup_run_state=lambda _args: {"v23-runtime-dir": Path("test-runtime")}
        ),
    )

    class ResetPlan:
        def reset_through(self, _name):
            pass

        def reset_named(self, name):
            calls.append(("reset", name))

    monkeypatch.setattr(v35, "_AUTHORITY_RUN_RESET_PLAN", ResetPlan())

    def forbidden(_args):
        raise AssertionError("historical V25/V24/V23 authority wrapper was invoked")

    monkeypatch.setattr(v35.v25, "authority_main", forbidden)
    monkeypatch.setattr(v35._V24, "authority_main", forbidden)
    monkeypatch.setattr(v35.v23, "authority_main", forbidden)

    def delegated(_args):
        calls.append(("delegate", "v17"))
        return 17

    monkeypatch.setattr(v35._V17, "authority_main", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 17

    v25_reset_index = calls.index(("reset", "v25-live-objective"))
    v25_log_index = next(i for i, item in enumerate(calls) if item[0] == "log" and "Planner V25:" in item[1])
    v24_log_index = next(i for i, item in enumerate(calls) if item[0] == "log" and "Planner V24:" in item[1])
    v23_log_index = next(i for i, item in enumerate(calls) if item[0] == "log" and "Planner V23:" in item[1])
    delegate_index = calls.index(("delegate", "v17"))
    assert v25_reset_index < v25_log_index < v24_log_index < v23_log_index < delegate_index
