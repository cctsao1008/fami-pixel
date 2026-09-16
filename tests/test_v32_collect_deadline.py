import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v32():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v32.py"
        spec = spec_from_file_location("planner_v32_collect_deadline", path)
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
        "schedule": [{"buttons": 0x82, "frames": 20}],
        "reward_prefix_safe": True,
        "reward_collected": False,
        "reward_key": list(reward_key),
        "trajectory_event": "prefix_alive",
        "trajectory_frames": 20,
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


def _record_right_b(v32, start: int, end: int) -> None:
    v32.v27._AUTHORITY_ACTION_LEDGER.clear()
    for frame in range(start, end):
        v32.v27._AUTHORITY_ACTION_LEDGER.record(frame, 0x82)


def test_v32_waits_for_quorum_before_latest_handoff(tmp_path):
    v32 = _load_v32()
    v32.v29._COLLECT_RESPONSE_CACHE.clear()
    _record_right_b(v32, 200, 211)

    paths = [tmp_path / f"response-{i}.json" for i in range(3)]
    _write(
        paths[0],
        _proof(
            worker=0,
            candidate="collect_continue_authority",
            reward_key=(0,),
        ),
    )

    selected = v32._best_collect_or_progress(
        paths,
        current_frame=211,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is None
    meta = v32.v23._latest_forward_meta
    assert meta["forward_model_status"] == "waiting-collect-worker-quorum"
    assert meta["collect_deadline_frames"] == 12
    assert meta["collect_deadline_closed"] is False


def test_v32_closes_partial_cohort_at_latest_useful_handoff(tmp_path):
    v32 = _load_v32()
    v32.v29._COLLECT_RESPONSE_CACHE.clear()
    _record_right_b(v32, 200, 212)

    paths = [tmp_path / f"response-{i}.json" for i in range(3)]
    _write(
        paths[0],
        _proof(
            worker=0,
            candidate="collect_continue_authority",
            reward_key=(0,),
        ),
    )

    selected = v32._best_collect_or_progress(
        paths,
        current_frame=212,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["candidate"] == "collect_continue_authority"
    assert selected["root_frame"] == 200
    assert selected["age"] == 12
    assert selected["collect_deadline_closed"] is True
    assert selected["collect_missing_workers"] == [1, 2]
    meta = v32.v23._latest_forward_meta
    assert meta["forward_model_status"] == "selected-deadline-closed-collect-proof"


def test_v32_full_quorum_can_select_before_deadline(tmp_path):
    v32 = _load_v32()
    v32.v29._COLLECT_RESPONSE_CACHE.clear()
    _record_right_b(v32, 200, 204)

    paths = [tmp_path / f"response-{i}.json" for i in range(3)]
    _write(paths[0], _proof(worker=0, candidate="anchor", reward_key=(0,)))
    _write(paths[1], _proof(worker=1, candidate="better", reward_key=(9,)))
    _write(paths[2], _proof(worker=2, candidate="peer", reward_key=(1,)))

    selected = v32._best_collect_or_progress(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["candidate"] == "better"
    assert selected["collect_cohort_complete"] is True
    assert selected["collect_deadline_closed"] is False


def test_v32_newer_partial_does_not_hide_older_deadline_closed_cohort(tmp_path):
    v32 = _load_v32()
    v32.v29._COLLECT_RESPONSE_CACHE.clear()
    _record_right_b(v32, 200, 212)

    paths = [tmp_path / f"response-{i}.json" for i in range(3)]
    # Cache an older gen20 partial cohort first.
    _write(paths[0], _proof(worker=0, candidate="old-anchor", reward_key=(1,), generation=20, root=200))
    assert v32._best_collect_or_progress(
        paths,
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    ) is None

    # Response file advances to a newer gen21 root, but that cohort is also partial
    # and only 4f old. At frame 212, the older root is exactly at its 12f closure
    # deadline and must remain available from the cache.
    _write(paths[0], _proof(worker=0, candidate="new-partial", reward_key=(99,), generation=21, root=208))
    selected = v32._best_collect_or_progress(
        paths,
        current_frame=212,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["generation"] == 20
    assert selected["root_frame"] == 200
