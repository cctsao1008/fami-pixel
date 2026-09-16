from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_terrain_probe_loads_without_running_mesen():
    tools = (Path(__file__).resolve().parents[1] / "tools").resolve()
    sys.path.insert(0, str(tools))
    try:
        path = tools / "smb1_terrain_probe.py"
        spec = spec_from_file_location("fami_pixel_terrain_probe_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)

        assert module._DONE == "TerrainProbe: DONE"
        assert callable(module.worker)
        assert callable(module.supervise)
        assert callable(module.main)
    finally:
        try:
            sys.path.remove(str(tools))
        except ValueError:
            pass
