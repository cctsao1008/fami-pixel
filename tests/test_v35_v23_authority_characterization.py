from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys


def _load_v35():
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

    # V23 itself does not own the per-frame authority loop.
    assert "for loop_index in range(args.max_frames):" not in source
    assert "_spawn_shadow_workers(args" not in source
    assert "base.save_checkpoint(core" not in source


def test_v17_is_the_actual_live_authority_loop_boundary_below_v23():
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


def test_current_v35_boundary_still_enters_captured_v23_bootstrap_before_live_loop():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert v35._BASE_V23_AUTHORITY is v35.v23.authority_main
    assert "return _BASE_V23_AUTHORITY(args)" in source
    assert "return v23.v17.authority_main(args)" not in source
