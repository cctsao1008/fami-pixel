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
        spec = spec_from_file_location("planner_v34_eager_collect", path)
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


def _proof(*, worker: int, handoff: int, name: str, reward_key, generation=20, root=200):
    return {
        "generation": generation,
        "worker": worker,
        "root_frame": root,
        "planner_mode": "collect",
        "target_reward_type": "star",
        "candidate": f"collect_delay{handoff}_{name}",
        "schedule": [
            {"buttons": RIGHT_B, "frames": handoff},
            {"buttons": LEFT_B, "frames": 4},
        ],
        "reward_prefix_safe": True,
        "reward_collected": False,
        "reward_key": list(reward_key),
        "trajectory_event": "prefix_alive",
        "trajectory_frames": handoff + 4,
        "collect_handoff_frames": handoff,
        "collect_continuation_anchor": False,
    }


def _response(worker: int, proofs: list[dict], *, generation=20, root=200):
    best = max(proofs, key=lambda proof: tuple(proof["reward_key"]))
    payload = dict(best)
    payload.update(
        {
            "generation": generation,
            "worker": worker,
            "root_frame": root,
            "planner_mode": "collect",
            "target_reward_type": "star",
            "branch_proofs": proofs,
        }
    )
    return payload


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record_right(module, start: int, end: int) -> None:
    module.v27._AUTHORITY_ACTION_LEDGER.clear()
    for frame in range(start, end):
        module.v27._AUTHORITY_ACTION_LEDGER.record(frame, RIGHT_B)


def test_v34_declares_four_frame_eager_handoff():
    v34 = _load_v34()
    assert v34.PLANNER_NAME == "v34-eager-handoff-collect"
    assert tuple(v34.COLLECT_HANDOFF_FRAMES) == (4, 8, 12)
    assert tuple(v34.v28.COLLECT_HANDOFF_FRAMES) == (4, 8, 12)


def test_v34_earliest_ready_stage_beats_later_better_reward_geometry(tmp_path):
    v34 = _load_v34()
    v34._HANDOFF_CACHE.clear()
    _record_right(v34, 200, 203)

    paths = [tmp_path / f"response-{worker}.json" for worker in range(3)]
    for worker, path in enumerate(paths):
        plus4 = _proof(
            worker=worker,
            handoff=4,
            name=f"early-{worker}",
            reward_key=(1, worker),
        )
        # Deliberately much stronger geometry at +8. V34 must still restore the
        # V27-like eager behavior once the +4 stage has complete coverage.
        plus8 = _proof(
            worker=worker,
            handoff=8,
            name=f"late-{worker}",
            reward_key=(99, worker),
        )
        _write(path, _response(worker, [plus4, plus8]))

    selected = v34._best_collect_or_progress(
        paths,
        current_frame=203,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["collect_handoff_frames"] == 4
    assert selected["candidate"].startswith("collect_delay4_")
    assert selected["root_frame"] == 200
    assert selected["age"] == 3
    assert v34.v23._latest_forward_meta["forward_model_status"] == (
        "selected-eager-handoff-collect-proof"
    )


def test_v34_late_plus4_cannot_reopen_but_plus8_can_still_win(tmp_path):
    v34 = _load_v34()
    v34._HANDOFF_CACHE.clear()
    _record_right(v34, 200, 205)

    paths = [tmp_path / f"response-{worker}.json" for worker in range(3)]
    for worker, path in enumerate(paths):
        plus4 = _proof(
            worker=worker,
            handoff=4,
            name=f"expired-{worker}",
            reward_key=(999, worker),
        )
        plus8 = _proof(
            worker=worker,
            handoff=8,
            name=f"reachable-{worker}",
            reward_key=(1, worker),
        )
        # First authority observation is age 5: +4 is already late, +8 is not.
        _write(path, _response(worker, [plus4, plus8]))

    selected = v34._best_collect_or_progress(
        paths,
        current_frame=205,
        freshness=16,
        last_applied_generation=19,
        live_radar={"collect_target_type": "star"},
    )
    assert selected is not None
    assert selected["collect_handoff_frames"] == 8
    assert selected["candidate"].startswith("collect_delay8_")

    timing = v34._HANDOFF_CACHE.handoff_snapshot(generation=20, root_frame=200)
    expired = [
        row
        for row in timing["proofs"]
        if row["candidate"].startswith("collect_delay4_")
    ]
    assert expired
    assert all(row["admitted"] is False for row in expired)
    assert all(row["deadline_slack_frames"] == -1 for row in expired)
