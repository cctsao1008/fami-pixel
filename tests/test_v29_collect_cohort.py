import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v29():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v29.py"
        spec = spec_from_file_location("planner_v29_collect_cohort", path)
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


def _proof(*, worker: int, candidate: str, reward_key, generation=20, root=200):
    proof = {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": candidate,
        "schedule": [{"buttons": 0x82, "frames": 24}],
        "reward_prefix_safe": True,
        "reward_collected": False,
        "reward_key": list(reward_key),
        "trajectory_event": "prefix_alive",
        "trajectory_frames": 24,
        "trajectory_end_x": 1000,
        "trajectory_end_y": 176,
        "collect_handoff_frames": 12,
        "collect_continuation_anchor": candidate == "collect_continue_authority",
    }
    response = dict(proof)
    response["branch_proofs"] = [dict(proof)]
    return response


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v29_collect_does_not_consume_generation_before_worker_quorum(tmp_path):
    v29 = _load_v29()
    v29._COLLECT_RESPONSE_CACHE.clear()
    v29.v27._AUTHORITY_ACTION_LEDGER.clear()
    for frame in range(200, 204):
        v29.v27._AUTHORITY_ACTION_LEDGER.record(frame, 0x82)

    anchor_path = tmp_path / "response-0.json"
    better_path = tmp_path / "response-1.json"
    peer_path = tmp_path / "response-2.json"
    paths = [anchor_path, better_path, peer_path]

    # Fast worker 0 returns a valid continuation anchor first. It must not consume
    # generation 20 before the other worker shards arrive.
    _write(
        anchor_path,
        _proof(
            worker=0,
            candidate="collect_continue_authority",
            reward_key=(0, 0, -100),
        ),
    )
    selected = v29._best_collect_or_progress(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is None
    assert v29.v23._latest_forward_meta["forward_model_status"] == "waiting-collect-worker-quorum"
    assert v29.v23._latest_forward_meta["collect_worker_count"] == 1

    _write(
        better_path,
        _proof(
            worker=1,
            candidate="collect_delay12_rearm_right_jump4",
            reward_key=(1, 1, -5),
        ),
    )
    selected = v29._best_collect_or_progress(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is None
    assert v29.v23._latest_forward_meta["collect_worker_count"] == 2

    _write(
        peer_path,
        _proof(
            worker=2,
            candidate="collect_delay12_left4",
            reward_key=(0, 1, -20),
        ),
    )
    selected = v29._best_collect_or_progress(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["candidate"] == "collect_delay12_rearm_right_jump4"
    assert selected["root_frame"] == 200
    assert selected["age"] == 4
    assert v29.v23._latest_forward_meta["forward_model_status"] == "selected-cohort-lineage-collect-proof"


def test_v29_keeps_older_complete_cohort_when_newer_cohort_is_partial(tmp_path):
    v29 = _load_v29()
    v29._COLLECT_RESPONSE_CACHE.clear()
    v29.v27._AUTHORITY_ACTION_LEDGER.clear()
    for frame in range(200, 208):
        v29.v27._AUTHORITY_ACTION_LEDGER.record(frame, 0x82)

    paths = [tmp_path / f"response-{i}.json" for i in range(3)]
    for worker, path in enumerate(paths):
        _write(
            path,
            _proof(
                worker=worker,
                candidate=f"old-{worker}",
                reward_key=(worker,),
                generation=20,
                root=200,
            ),
        )

    selected = v29._best_collect_or_progress(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["generation"] == 20

    # Worker 0 advances to gen21/root204 while the other files still hold gen20.
    # The cache must retain the complete older cohort; gen21 is only partial.
    _write(
        paths[0],
        _proof(
            worker=0,
            candidate="new-partial",
            reward_key=(99,),
            generation=21,
            root=204,
        ),
    )

    # Use last_applied_generation=19 here to model an unconsumed older cohort;
    # this isolates cache/cohort ordering rather than generation consumption.
    selected = v29._best_collect_or_progress(
        paths,
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["generation"] == 20
    assert selected["root_frame"] == 200
