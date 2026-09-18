from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys


def _load_v17():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v17.py"
        spec = spec_from_file_location("planner_v17_request_enrichment", path)
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


def test_v17_publishes_checkpoint_request_through_stable_enrichment_seam():
    v17 = _load_v17()
    source = inspect.getsource(v17.authority_main)

    assert "request_payload = enrich_checkpoint_request(" in source
    assert "published = v11._atomic_json(request_path, request_payload)" in source
    assert "v11._atomic_json =" not in source
