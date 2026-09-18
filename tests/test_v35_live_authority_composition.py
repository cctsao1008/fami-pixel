from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys
from types import SimpleNamespace

from fami_pixel.control import LiveAuthorityControl


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_live_authority_composition", path)
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


def test_v35_builds_stable_live_control_from_explicit_current_providers():
    v35 = _load_v35()
    control = v35._build_live_authority_control()

    assert isinstance(control, LiveAuthorityControl)
    assert control.prepare_run is v35._prepare_v17_live_run
    assert control.spawn_workers is v35._V17.v15._spawn_shadow_workers
    assert control.select_plan is v35._V17.v16.best_coherent_live_radar_plan
    assert control.derive_events is v35._V17.derive_game_events
    assert control.read_radar is v35._V17.read_smb1_radar
    assert control.schedule_label is v35._V17.v14._schedule_label
    assert control.append_timeline is v35._V17._append_timeline
    assert control.persist_terminal is v35._V17._persist_terminal
    assert control.set_controller_state is v35.base.set_nes_controller_state
    assert control.save_checkpoint is v35.base.save_checkpoint
    assert control.publish_json is v35.v11._atomic_json


def test_v35_active_authority_enters_stable_loop_only_after_v23_bootstrap():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    bootstrap = "bootstrap = _V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)"
    build = "live_control = _build_live_authority_control()"
    run = "return live_control.run(args)"

    assert bootstrap in source
    assert build in source
    assert run in source
    assert source.index(bootstrap) < source.index(build) < source.index(run)
    assert "return _V17.authority_main(args)" not in source
    assert "return v23.authority_main(args)" not in source


def test_v17_run_setup_is_preserved_before_stable_loop(tmp_path, monkeypatch):
    v35 = _load_v35()
    model = tmp_path / "surrogate.json"
    model.write_text("{}", encoding="utf-8")
    resets = []
    monkeypatch.setattr(v35._V17.v12, "reset_response_cache", lambda: resets.append("response-cache"))

    args = SimpleNamespace(
        surrogate_model=model,
        surrogate_risk_cutoff=0.42,
        surrogate_risk_penalty=1.25,
        surrogate_no_progress_penalty=2.5,
        surrogate_dx_weight=0.75,
    )
    result = v35._prepare_v17_live_run(args)

    assert result == model.resolve()
    assert args.surrogate_model == model.resolve()
    assert v35._V17.v14._RISK_CUTOFF == 0.42
    assert v35._V17.v13._RISK_PENALTY == 1.25
    assert v35._V17.v13._STALL_PENALTY == 2.5
    assert v35._V17.v13._DX_WEIGHT == 0.75
    assert resets == ["response-cache"]
