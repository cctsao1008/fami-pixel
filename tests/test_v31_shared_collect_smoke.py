from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import inspect
import sys


def _load_v31():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v31.py"
        spec = spec_from_file_location("planner_v31_shared_collect", path)
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


def test_v30_default_horizon_is_latency_target_plus_commit():
    v31 = _load_v31()
    args = SimpleNamespace(plan_freshness=16)
    assert v31.v30._proof_horizon(args) == 20


def test_v30_unique_chunk_sharding_does_not_duplicate_extra_workers():
    v31 = _load_v31()
    all_names = []
    for worker in range(12):
        chunks = v31.v30._unique_reward_chunks_for_worker(worker, 12)
        all_names.extend(chunk.name for chunk in chunks)

    assert len(all_names) == len(v31.v30.v25.REWARD_BEAM_CHUNKS_WITH_HOLD)
    assert len(set(all_names)) == len(all_names)


def test_v31_fallback_temporarily_restores_original_v28_exact_evaluator():
    v31 = _load_v31()
    seen = []

    def fake_shared(*args, **kwargs):
        seen.append(v31.v28._search_collect_payload is v31._BASE_V28_COLLECT_SEARCH)
        return "ok"

    installed = v31._search_collect_payload_shared_safe
    v31.v30._search_collect_payload_shared = fake_shared
    v31.v28._search_collect_payload = installed

    assert v31._search_collect_payload_shared_safe() == "ok"
    assert seen == [True]
    assert v31.v28._search_collect_payload is installed


def test_v31_installer_keeps_v30_tree_and_replaces_only_worker_collect_entry():
    v31 = _load_v31()
    source = inspect.getsource(v31._install_v31_overrides)
    assert "v30._install_v30_overrides()" in source
    assert "v28._search_collect_payload = _search_collect_payload_shared_safe" in source
    assert "v23.authority_main = v30.authority_main" in source
    assert "v23.__file__ = __file__" in source
