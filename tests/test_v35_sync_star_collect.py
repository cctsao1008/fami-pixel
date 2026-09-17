from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_sync_star_collect", path)
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


def _outcome(*, x, y=120, collected=False, died=False, reward_key=(1, 2, 3), target=None):
    return {
        "died": bool(died),
        "won": False,
        "collected": bool(collected),
        "frames": 4,
        "observation": SimpleNamespace(mario_x_abs=int(x), mario_y=int(y)),
        "target": target,
        "reward_key": tuple(reward_key),
    }


def test_sync_star_search_is_root_exact_and_restores_after_every_branch(monkeypatch, tmp_path):
    v35 = _load_v35()
    fake_core = object()
    v35._SYNC_CHECKPOINT = tmp_path / "sync-star.mss"
    v35._SYNC_STEP_TIMEOUT = 1.0

    chunks = tuple(v35.v25.REWARD_BEAM_CHUNKS_WITH_HOLD)
    assert chunks

    save_calls = []
    restore_calls = []
    ledger = v35.v34.v27._AUTHORITY_ACTION_LEDGER
    ledger.clear()
    ledger.record(99, 0x82)

    def fake_save(core, path):
        assert core is fake_core
        save_calls.append(path)
        # This mimics the instrumented base checkpoint helper touching the pad at
        # the current frame. It must be suppressed because no authority frame is
        # being advanced by the synchronous search.
        ledger.record(100, 0x00)
        return 100, 1600, 5

    def fake_restore(core, path, frame, x, engine):
        assert core is fake_core
        assert (frame, x, engine) == (100, 1600, 5)
        ledger.record(104, 0x00)
        restore_calls.append(path)

    def fake_eval(core, chunk, *, target_type, request_radar, step_timeout):
        assert core is fake_core
        assert target_type == "star"
        assert request_radar["collect_target_type"] == "star"
        if chunk.name == "hold_left_jump4":
            return _outcome(
                x=1596,
                collected=True,
                reward_key=(9, 9, 9),
                target={"dx": -4, "state": 0x80, "y": 100},
            )
        return _outcome(x=1604, collected=False, reward_key=(1, 1, 1))

    monkeypatch.setattr(v35.base, "save_checkpoint", fake_save)
    monkeypatch.setattr(v35.base, "restore_checkpoint", fake_restore)
    monkeypatch.setattr(v35.v25, "_evaluate_reward_chunk", fake_eval)

    plan = v35._sync_star_plan(
        fake_core,
        current_frame=100,
        live_radar={"collect_target_type": "star", "star_invincible_timer": 0},
    )

    assert plan is not None
    assert plan["candidate"] == "collect_hold_left_jump4"
    assert plan["root_frame"] == 100
    assert plan["trajectory_root_frame"] == 100
    assert plan["trajectory_source_age_frames"] == 0
    assert plan["age"] == 0
    assert plan["generation"] == -1
    assert plan["reward_collected"] is True
    assert plan["worker"] == "authority-sync-star"
    assert len(save_calls) == 1
    # One restore before every exact branch plus one mandatory final restore.
    assert len(restore_calls) == len(chunks) + 1
    # Historical authority remains, while speculative current/future writes never
    # enter lineage history.
    assert ledger.buttons_between(99, 100) == (0x82,)
    assert ledger.buttons_between(100, 101) is None
    assert ledger.buttons_between(104, 105) is None


def test_sync_star_root_mismatch_never_rebases(monkeypatch, tmp_path):
    v35 = _load_v35()
    fake_core = object()
    v35._SYNC_CHECKPOINT = tmp_path / "sync-star.mss"
    restored = []

    monkeypatch.setattr(v35.base, "save_checkpoint", lambda *_: (96, 1590, 5))
    monkeypatch.setattr(
        v35.base,
        "restore_checkpoint",
        lambda core, path, frame, x, engine: restored.append((frame, x, engine)),
    )

    plan = v35._sync_star_plan(
        fake_core,
        current_frame=100,
        live_radar={"collect_target_type": "star"},
    )

    assert plan is None
    assert restored == [(96, 1590, 5)]
    assert v35.v23._latest_forward_meta["forward_model_status"] == "sync-star-root-mismatch"
    assert v35.v23._latest_forward_meta["forward_model_source_age_frames"] == 4


def test_star_uses_sync_path_before_v34_async(monkeypatch):
    v35 = _load_v35()
    sentinel = {"candidate": "collect_hold_right_jump4", "root_frame": 200}
    v35._LIVE_AUTHORITY_CORE = object()

    monkeypatch.setattr(v35, "_sync_star_plan", lambda *args, **kwargs: dict(sentinel))

    def fail_async(*args, **kwargs):
        raise AssertionError("V34 async path must not run after current-root Star proof succeeds")

    monkeypatch.setattr(v35.v34, "_best_collect_or_progress", fail_async)

    plan = v35._best_collect_or_progress(
        [],
        200,
        16,
        -1,
        {"collect_target_type": "star"},
    )
    assert plan == sentinel


def test_non_star_retains_v34_path(monkeypatch):
    v35 = _load_v35()
    sentinel = {"candidate": "async-mushroom"}
    v35._LIVE_AUTHORITY_CORE = object()
    monkeypatch.setattr(
        v35.v34,
        "_best_collect_or_progress",
        lambda *args, **kwargs: dict(sentinel),
    )

    plan = v35._best_collect_or_progress(
        [],
        300,
        16,
        -1,
        {"collect_target_type": "mushroom"},
    )
    assert plan == sentinel
