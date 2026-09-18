from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from fami_pixel.planning import shape_eager_collect_result


@dataclass(frozen=True)
class _Selection:
    proof: dict | None
    valid_count: int = 1
    rejected: dict[str, int] | None = None


def _load_v34():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v34.py"
        spec = spec_from_file_location("planner_v34_collect_selection_seam", path)
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


def test_stable_selected_result_shape_matches_historical_v34_contract():
    v34 = _load_v34()
    selection = _Selection(
        proof={
            "root_frame": 240,
            "candidate": "collect_delay4_right",
            "proof_remaining_frames": 7,
            "schedule": [{"buttons": 0x82, "frames": 4}],
        },
        rejected={},
    )
    radar = {"collect_target_type": "star", "nearest_enemy_dx": 91}

    historical = v34._selected_result(
        selection,
        current_frame=243,
        live_radar=radar,
        handoff=4,
    )
    stable = shape_eager_collect_result(
        selection,
        current_frame=243,
        live_radar=radar,
        handoff_frames=4,
    )

    assert stable == historical
