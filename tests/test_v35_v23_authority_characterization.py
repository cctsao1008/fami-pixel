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
        spec = spec_from_file_location("planner_v35_v23_authority_characterization", path)
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


def test_v23_authority_is_bootstrap_setup_then_v17_delegate_not_the_live_loop():
    v35 = _load_v35()
    source = inspect.getsource(v35.v23.authority_main)

    assert 'if os.name == "nt":' in source
    assert "join_windows_job_from_env()" in source
    assert "if args.surrogate_model is None:" in source
    assert "model_path = args.surrogate_model.expanduser().resolve()" in source
    assert "if not model_path.is_file():" in source
    assert "runtime_dir = isolated_run_dir(args.checkpoint_dir)" in source
    assert "args.checkpoint_dir = runtime_dir" in source
    assert "args.shadow_home = args.shadow_home.expanduser().resolve() / runtime_dir.name" in source
    assert "v20._install_landing_overrides()" in source
    assert "_install_forward_overrides()" in source
    assert '"Planner V23: live Mesen forward model enabled | "' in source
    assert "return v17.authority_main(args)" in source

    # Historical V23 itself does not own the per-frame authority loop.
    assert "for loop_index in range(args.max_frames):" not in source
    assert "_spawn_shadow_workers(args" not in source
    assert "base.save_checkpoint(core" not in source


def test_historical_v17_remains_the_reference_live_loop_for_provenance():
    v35 = _load_v35()
    source = inspect.getsource(v35.v23.v17.authority_main)

    assert "workers = v15._spawn_shadow_workers(args, request_path, response_paths)" in source
    assert "core = MesenCore(args.dll)" in source
    assert "for loop_index in range(args.max_frames):" in source
    assert "if loop_index % args.control_quantum == 0:" in source
    assert "plan = v16.best_coherent_live_radar_plan(" in source
    assert "request_payload = enrich_checkpoint_request(" in source
    assert "published = v11._atomic_json(request_path, request_payload)" in source
    assert "base.save_checkpoint(core, checkpoint)" in source
    assert "worker.terminate()" in source
    assert "worker.wait(timeout=0.5)" in source


def test_current_v35_extracts_v23_bootstrap_and_enters_stable_live_loop():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert type(v35._V23_BOOTSTRAP_SETUP_PLAN).__module__ == "fami_pixel.control.authority_runtime"
    assert v35._V23_BOOTSTRAP_SETUP_PLAN.names == (
        "v23-process-job",
        "v23-surrogate-model",
        "v23-runtime-dir",
        "v23-live-stack",
    )
    assert "bootstrap = _V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)" in source
    assert 'runtime_dir = bootstrap["v23-runtime-dir"]' in source
    assert 'v11._log(f"Runtime IPC : {runtime_dir}")' in source
    assert '"Planner V23: live Mesen forward model enabled | "' in source
    assert "live_control = _build_live_authority_control()" in source
    assert "return live_control.run(args)" in source
    assert "return _V17.authority_main(args)" not in source
    assert "return v23.authority_main(args)" not in source
    assert v35._V17 is v35.v23.v17


def test_v23_bootstrap_plan_preserves_path_setup_and_install_order(monkeypatch, tmp_path):
    v35 = _load_v35()
    calls = []

    model = tmp_path / "surrogate.json"
    model.write_text("{}", encoding="utf-8")
    original_checkpoint = tmp_path / "checkpoints"
    original_shadow_home = tmp_path / "shadow-home"
    runtime_dir = tmp_path / "isolated-run"

    monkeypatch.setattr(
        v35.v23,
        "isolated_run_dir",
        lambda path: calls.append(("runtime-dir", path)) or runtime_dir,
    )
    monkeypatch.setattr(
        v35.v23.v20,
        "_install_landing_overrides",
        lambda: calls.append(("install", "landing")),
    )
    monkeypatch.setattr(
        v35.v23,
        "_install_forward_overrides",
        lambda: calls.append(("install", "forward")),
    )
    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    if v35.v23.os.name == "nt":
        monkeypatch.setattr(v35.v23, "join_windows_job_from_env", lambda: True)

    args = SimpleNamespace(
        surrogate_model=model,
        checkpoint_dir=original_checkpoint,
        shadow_home=original_shadow_home,
    )
    results = v35._V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)

    assert tuple(results) == v35._V23_BOOTSTRAP_SETUP_PLAN.names
    assert results["v23-surrogate-model"] == model.resolve()
    assert results["v23-runtime-dir"] == runtime_dir
    assert args.surrogate_model == model.resolve()
    assert args.checkpoint_dir == runtime_dir
    assert args.shadow_home == original_shadow_home.resolve() / runtime_dir.name
    assert calls == [
        ("runtime-dir", original_checkpoint),
        ("install", "landing"),
        ("install", "forward"),
    ]
