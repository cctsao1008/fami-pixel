import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


RIGHT_B = 0x82
LEFT_B = 0x42


def _load_v34():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v34.py"
        spec = spec_from_file_location("planner_v34_collect_status_equivalence", path)
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


def _proof(worker: int, *, generation: int = 20, root: int = 200) -> dict:
    return {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": f"collect_delay4_w{worker}",
        "schedule": [
            {"buttons": RIGHT_B, "frames": 4},
            {"buttons": LEFT_B, "frames": 4},
        ],
        "reward_prefix_safe": True,
        "reward_collected": False,
        "reward_key": [1, worker],
        "trajectory_event": "prefix_alive",
        "trajectory_frames": 8,
        "collect_handoff_frames": 4,
        "collect_continuation_anchor": False,
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v34_selected_status_payload_survives_stable_control_migration(tmp_path):
    v34 = _load_v34()
    v34._HANDOFF_CACHE.clear()
    v34.v27._AUTHORITY_ACTION_LEDGER.clear()
    for frame in range(200, 203):
        v34.v27._AUTHORITY_ACTION_LEDGER.record(frame, RIGHT_B)

    paths = []
    for worker in range(3):
        path = tmp_path / f"response-{worker}.json"
        proof = _proof(worker)
        payload = dict(proof)
        payload["branch_proofs"] = [proof]
        _write(path, payload)
        paths.append(path)

    selected = v34._best_collect_or_progress(
        paths,
        current_frame=203,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )

    assert selected is not None
    meta = v34.v23._latest_forward_meta
    assert meta["forward_model_status"] == "selected-eager-handoff-collect-proof"
    assert meta["objective_mode"] == "COLLECT"
    assert meta["collect_target_type"] == "star"
    assert meta["forward_model_generation"] == 20
    assert meta["forward_model_source_frame"] == 200
    assert meta["forward_model_source_age_frames"] == 3
    assert meta["collect_handoff_frames"] == 4
    assert meta["collect_handoff_stage_complete"] is True
    assert meta["collect_handoff_stage_closed"] is False
    assert meta["collect_handoff_workers"] == [0, 1, 2]
    assert meta["collect_active_workers"] == [0, 1, 2]
    assert meta["collect_lineage_valid_count"] == 3
