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
    delegate = "return _BASE_V23_AUTHORITY(args)"

    for token in (progress, ledger, recorder, v26_reset, v25_reset, delegate):
        assert token in source
    assert (
        source.index(progress)
        < source.index(ledger)
        < source.index(recorder)
        < source.index(v26_reset)
        < source.index(v25_reset)
        < source.index(delegate)
    )
    assert "return v26._BASE_V25_AUTHORITY(args)" not in source
    assert "return v26.authority_main(args)" not in source
    assert "return _V27.authority_main(args)" not in source


def test_v35_v27_resets_occur_inside_v28_enrichment_before_lower_authority(monkeypatch, tmp_path):
    v35 = _load_v35()
    calls = []

    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: None),
    )

    class ResetPlan:
        def reset_through(self, _name):
            pass

        def reset_named(self, name):
            calls.append(name)

    monkeypatch.setattr(v35, "_AUTHORITY_RUN_RESET_PLAN", ResetPlan())

    def delegated(_args):
        calls.append("v23")
        return 23

    monkeypatch.setattr(v35, "_BASE_V23_AUTHORITY", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 23
    assert calls[-5:] == [
        "v27-progress-response-cache",
        "v27-authority-action-ledger",
        "v26-gap-commitment",
        "v25-live-objective",
        "v23",
    ]
