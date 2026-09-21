from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys
from types import SimpleNamespace


def _load_v35():
    for name in tuple(sys.modules):
        if name.startswith("mesen_smb_checkpoint_planner"):
            sys.modules.pop(name, None)

    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_v27_authority_characterization", path)
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


def test_v27_historical_authority_responsibility_is_two_resets_log_recorder_and_delegate():
    v35 = _load_v35()
    source = inspect.getsource(v35._V27.authority_main)

    assert "_PROGRESS_RESPONSE_CACHE.clear()" in source
    assert "_AUTHORITY_ACTION_LEDGER.clear()" in source
    assert '"Planner V27: bounded multi-chunk PROGRESS search enabled | "' in source
    assert "authority_action_recording_layer(_AUTHORITY_ACTION_LEDGER)" in source
    assert "return v26.authority_main(args)" in source


def test_current_v35_extracts_v27_runtime_and_keeps_recorder_around_lower_authority():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    progress = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-progress-response-cache")'
    ledger = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-authority-action-ledger")'
    recorder = "authority_action_recording_layer(_V27._AUTHORITY_ACTION_LEDGER)"
    v26_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")'
    v25_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v25-live-objective")'
    bootstrap = "_V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)"
    build = "live_control = _build_live_authority_control()"
    delegate = "return live_control.run(args)"

    for token in (progress, ledger, recorder, v26_reset, v25_reset, bootstrap, build, delegate):
        assert token in source
    assert (
        source.index(progress)
        < source.index(ledger)
        < source.index(recorder)
        < source.index(v26_reset)
        < source.index(v25_reset)
        < source.index(bootstrap)
        < source.index(build)
        < source.index(delegate)
    )
    assert "return _V17.authority_main(args)" not in source
    assert "return v23.authority_main(args)" not in source
    assert "return v26._BASE_V25_AUTHORITY(args)" not in source
    assert "return v26.authority_main(args)" not in source
    assert "return _V27.authority_main(args)" not in source


def test_v35_v27_resets_occur_inside_v28_enrichment_before_v23_bootstrap_and_stable_live_loop(monkeypatch, tmp_path):
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
        raise AssertionError("historical V17 authority wrapper was invoked")

    monkeypatch.setattr(v35._V17, "authority_main", forbidden)

    class LiveControl:
        def run(self, _args):
            calls.append("stable-live")
            return 17

    monkeypatch.setattr(v35, "_build_live_authority_control", lambda: LiveControl())

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 17
    assert calls[-6:] == [
        "v27-progress-response-cache",
        "v27-authority-action-ledger",
        "v26-gap-commitment",
        "v25-live-objective",
        "v23-bootstrap",
        "stable-live",
    ]
