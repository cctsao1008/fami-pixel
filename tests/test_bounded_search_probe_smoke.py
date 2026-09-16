from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_bounded_search_probe_imports_without_runtime_side_effects():
    tools = (Path(__file__).resolve().parents[1] / "tools").resolve()
    sys.path.insert(0, str(tools))
    try:
        path = tools / "smb1_bounded_search_probe.py"
        spec = spec_from_file_location("bounded_search_probe_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)

        assert module.DEFAULT_SEARCH_DEPTH == 3
        assert module.DEFAULT_TOP_K == 12
        assert module._DONE == "BoundedSearchProbe: DONE"
    finally:
        try:
            sys.path.remove(str(tools))
        except ValueError:
            pass
