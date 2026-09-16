import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


RIGHT_B = 0x82


def _load_v33():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v33.py"
        spec = spec_from_file_location("planner_v33_collect_deadline", path)
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


def _response(*, worker: int, reward_key, compute_ms: float, generation=20, root=200):
    proof = {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": f"worker-{worker}-reward",
        "schedule": [{"buttons": RIGHT_B, "frames": 24}],
        "reward_prefix_safe": True,
        "reward_collected": False,
        "reward_key": list(reward_key),
        "trajectory_event": "prefix_alive",
        "trajectory_frames": 24,
        "trajectory_end_x": 1000 + worker,
        "trajectory_end_y": 176,
        "collect_handoff_frames": 12,
        "collect_continuation_anchor": False,
        "compute_ms": float(compute_ms),
        "collect_exact_frame_steps": 20 + worker,
    }
    response = dict(proof)
    response["branch_proofs"] = [dict(proof)]
    return response


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record_right(ledger, start: int, end: int) -> None:
    for frame in range(start, end):
        ledger.record(frame, RIGHT_B)


def test_v33_slow_worker_cannot_reopen_deadline_closed_cohort(tmp_path):
    v33 = _load_v33()
    v33._DEADLINE_CACHE.clear()
    ledger = v33.v32.v27._AUTHORITY_ACTION_LEDGER
    ledger.clear()
    _record_right(ledger, 200, 216)

    paths = [tmp_path / f"response-{i}.json" for i in range(3)]

    # worker0 arrives at source age 4
    _write(paths[0], _response(worker=0, reward_key=(1,), compute_ms=4.0))
    selected = v33._best_collect_or_progress(
        paths,
        current_frame=204,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is None
    assert v33.v23._latest_forward_meta["collect_worker_arrival_age_frames"] == {"0": 4}

    # worker1 arrives at source age 8; still before the 12f deadline, so wait.
    _write(paths[1], _response(worker=1, reward_key=(5,), compute_ms=8.0))
    selected = v33._best_collect_or_progress(
        paths,
        current_frame=208,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is None
    assert v33.v23._latest_forward_meta["collect_worker_arrival_age_frames"] == {
        "0": 4,
        "1": 8,
    }

    # At age 12 the missing worker closes the cohort. Worker1 is the strongest
    # admitted exact proof, so V32/V33 may select it without waiting forever.
    selected = v33._best_collect_or_progress(
        paths,
        current_frame=212,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["candidate"] == "worker-1-reward"
    assert selected["collect_deadline_closed"] is True
    assert selected["collect_missing_workers"] == [2]
    assert selected["collect_cohort_closure_reason"] == "deadline"

    # Fault injection: worker2 first appears at age 16 with a much better score.
    # Keep last_applied_generation at 19 deliberately: even without generation
    # consumption, the already-missed deadline must make membership irreversible.
    _write(paths[2], _response(worker=2, reward_key=(99,), compute_ms=99.0))
    selected = v33._best_collect_or_progress(
        paths,
        current_frame=216,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["candidate"] == "worker-1-reward"
    assert selected["collect_late_workers"] == [2]
    assert selected["collect_on_time_workers"] == [0, 1]
    assert selected["collect_worker_arrival_age_frames"] == {"0": 4, "1": 8, "2": 16}
    assert selected["collect_worker_deadline_slack_frames"]["2"] == -4
    assert selected["collect_worker_compute_ms"]["2"] == 99.0


def test_v33_full_quorum_at_deadline_reports_zero_margin(tmp_path):
    v33 = _load_v33()
    v33._DEADLINE_CACHE.clear()
    ledger = v33.v32.v27._AUTHORITY_ACTION_LEDGER
    ledger.clear()
    _record_right(ledger, 200, 212)
    paths = [tmp_path / f"response-{i}.json" for i in range(3)]

    _write(paths[0], _response(worker=0, reward_key=(1,), compute_ms=4.0))
    assert v33._best_collect_or_progress(
        paths, 204, 16, 19, {"collect_target_type": "star"}
    ) is None

    _write(paths[1], _response(worker=1, reward_key=(2,), compute_ms=8.0))
    assert v33._best_collect_or_progress(
        paths, 208, 16, 19, {"collect_target_type": "star"}
    ) is None

    _write(paths[2], _response(worker=2, reward_key=(3,), compute_ms=12.0))
    selected = v33._best_collect_or_progress(
        paths, 212, 16, 19, {"collect_target_type": "star"}
    )
    assert selected is not None
    assert selected["candidate"] == "worker-2-reward"
    assert selected["collect_cohort_closure_reason"] == "quorum"
    assert selected["collect_quorum_arrival_age_frames"] == 12
    assert selected["collect_quorum_deadline_margin_frames"] == 0
    assert selected["collect_late_workers"] == []
