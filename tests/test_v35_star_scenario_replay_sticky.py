from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from fami_pixel.games.smb1.reward_live import StickyCollectObjective


def _load_replay():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_v35_star_scenario_replay.py"
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        spec = spec_from_file_location("v35_star_scenario_replay_sticky_test", path)
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


def test_collect_radar_keeps_star_across_one_native_radar_dropout(monkeypatch):
    replay = _load_replay()

    class Core:
        frame = 100

        def frame_count(self):
            return self.frame

    core = Core()
    objective = StickyCollectObjective(ttl_frames=24)

    payloads = [
        {
            "nearest_reward_type": "star",
            "player_status": 0,
            "star_invincible_timer": 0,
            "rewards": [{"type": "star", "dx": 0, "state": 2}],
        },
        {
            "nearest_reward_type": None,
            "player_status": 0,
            "star_invincible_timer": 0,
            "rewards": [],
        },
    ]
    tracked = [
        {"type": "star", "dx": 0, "state": 2, "x": 1600, "y": 120},
        None,
    ]

    monkeypatch.setattr(replay, "_native_radar", lambda *_: dict(payloads.pop(0)))
    monkeypatch.setattr(replay, "read_active_reward_target", lambda *_args, **_kwargs: tracked.pop(0))

    first = replay._collect_radar(core, 1600, objective)
    assert first["collect_target_type"] == "star"
    assert first["collect_target_sticky"] is False

    core.frame = 104
    second = replay._collect_radar(core, 1596, objective)
    assert second["nearest_reward_type"] is None
    assert second["collect_target_type"] == "star"
    assert second["collect_target_sticky"] is True


def test_collect_radar_ends_star_only_on_native_timer_proof(monkeypatch):
    replay = _load_replay()

    class Core:
        frame = 100

        def frame_count(self):
            return self.frame

    core = Core()
    objective = StickyCollectObjective(ttl_frames=24)

    payloads = [
        {
            "nearest_reward_type": "star",
            "player_status": 0,
            "star_invincible_timer": 0,
            "rewards": [{"type": "star", "dx": 0, "state": 2}],
        },
        {
            "nearest_reward_type": None,
            "player_status": 0,
            "star_invincible_timer": 35,
            "rewards": [],
        },
    ]
    tracked = [
        {"type": "star", "dx": 0, "state": 2, "x": 1600, "y": 120},
        None,
    ]

    monkeypatch.setattr(replay, "_native_radar", lambda *_: dict(payloads.pop(0)))
    monkeypatch.setattr(replay, "read_active_reward_target", lambda *_args, **_kwargs: tracked.pop(0))

    replay._collect_radar(core, 1600, objective)
    core.frame = 104
    collected = replay._collect_radar(core, 1596, objective)

    assert collected["star_invincible_timer"] == 35
    assert collected["collect_target_type"] is None
    assert collected["reward_collected_type"] == "star"


def test_shutdown_core_stops_and_releases_even_if_controller_neutralize_fails(monkeypatch):
    replay = _load_replay()
    calls = []

    class Core:
        def stop(self):
            calls.append("stop")

        def release(self):
            calls.append("release")

    def fail_neutralize(*_args, **_kwargs):
        calls.append("neutralize")
        raise RuntimeError("synthetic controller cleanup failure")

    monkeypatch.setattr(replay, "set_nes_controller_state", fail_neutralize)
    replay._shutdown_core(Core())

    assert calls == ["neutralize", "stop", "release"]
