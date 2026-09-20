from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest

from fami_pixel.adapters.mesen import MesenLoadError, NativeSpecFrameWitness
from fami_pixel.games.smb1 import decode_smb1_state, observation_from_state
from fami_pixel.games.smb1.radar import (
    ADDR_PLAYER_STATUS,
    ADDR_SCREEN_RIGHT_PAGE,
    ADDR_SCREEN_RIGHT_X,
    ADDR_STAR_INVINCIBLE_TIMER,
    decode_smb1_radar,
)
from fami_pixel.games.smb1.reward_beam import REWARD_BEAM_CHUNKS_WITH_HOLD
from fami_pixel.games.smb1.state import (
    ADDR_FRAME_COUNTER,
    ADDR_GAME_ENGINE_SUBROUTINE,
    ADDR_LEVEL_NUMBER,
    ADDR_OPER_MODE,
    ADDR_OPER_MODE_TASK,
    ADDR_PLAYER_PAGE,
    ADDR_PLAYER_STATE,
    ADDR_PLAYER_X,
    ADDR_PLAYER_X_SPEED,
    ADDR_PLAYER_Y,
    ADDR_PLAYER_Y_HIGH,
    ADDR_PLAYER_Y_SPEED,
    ADDR_SAVED_JOYPAD1,
    ADDR_WORLD_NUMBER,
)


def _load_v36():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v36.py"
        spec = spec_from_file_location("planner_v36_native_star_test", path)
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


def _ram(*, smb_frame: int, engine: int = 0x08, timer: int = 0, joypad: int = 0) -> bytes:
    ram = bytearray(0x800)
    ram[ADDR_FRAME_COUNTER] = int(smb_frame) & 0xFF
    ram[ADDR_OPER_MODE] = 1
    ram[ADDR_OPER_MODE_TASK] = 0
    ram[ADDR_GAME_ENGINE_SUBROUTINE] = int(engine) & 0xFF
    ram[ADDR_WORLD_NUMBER] = 0
    ram[ADDR_LEVEL_NUMBER] = 0
    ram[ADDR_PLAYER_PAGE] = 6
    ram[ADDR_PLAYER_X] = 0x50
    ram[ADDR_PLAYER_Y_HIGH] = 1
    ram[ADDR_PLAYER_Y] = 0xA0
    ram[ADDR_PLAYER_STATE] = 0
    ram[ADDR_PLAYER_X_SPEED] = 0
    ram[ADDR_PLAYER_Y_SPEED] = 0
    ram[ADDR_SAVED_JOYPAD1] = int(joypad) & 0xFF
    ram[ADDR_PLAYER_STATUS] = 1
    ram[ADDR_STAR_INVINCIBLE_TIMER] = int(timer) & 0xFF
    ram[ADDR_SCREEN_RIGHT_PAGE] = 7
    ram[ADDR_SCREEN_RIGHT_X] = 0x40
    return bytes(ram)


class _FakeRunner:
    def __init__(self, witnesses):
        self.witnesses = tuple(witnesses)
        self.resets = 0
        self.schedules = []

    def reset_to_root(self):
        self.resets += 1

    def run_schedule(self, buttons, *, port=0):
        self.schedules.append((tuple(buttons), int(port)))
        return self.witnesses


def _root_context():
    root_ram = _ram(smb_frame=10)
    root_observation = observation_from_state(100, decode_smb1_state(root_ram))
    root_radar = decode_smb1_radar(
        root_ram,
        player_x=root_observation.mario_x_abs,
    ).to_payload()
    return root_observation, root_radar


def test_native_reward_evaluator_preserves_early_death_stop_even_if_schedule_ran_ahead():
    v36 = _load_v36()
    root_observation, root_radar = _root_context()
    witnesses = (
        NativeSpecFrameWitness(101, 0x82, _ram(smb_frame=11, joypad=0x82)),
        NativeSpecFrameWitness(102, 0x83, _ram(smb_frame=12, engine=0x0B, joypad=0x83)),
        # This later witness would prove Star collection if it were incorrectly
        # considered after the authoritative death boundary.
        NativeSpecFrameWitness(103, 0x83, _ram(smb_frame=13, timer=9, joypad=0x83)),
        NativeSpecFrameWitness(104, 0x83, _ram(smb_frame=14, timer=8, joypad=0x83)),
    )
    runner = _FakeRunner(witnesses)

    outcome = v36._evaluate_native_reward_chunk(
        runner,
        REWARD_BEAM_CHUNKS_WITH_HOLD[3],
        root_observation=root_observation,
        root_radar=root_radar,
        target_type="star",
        request_radar=root_radar,
    )

    assert outcome["frames"] == 2
    assert outcome["died"] is True
    assert outcome["collected"] is False
    assert outcome["observation"].native_frame_id == 102
    assert outcome["controller"] == 0x83
    assert runner.resets == 1


def test_native_reward_evaluator_stops_on_native_star_capability_proof():
    v36 = _load_v36()
    root_observation, root_radar = _root_context()
    witnesses = (
        NativeSpecFrameWitness(101, 0x00, _ram(smb_frame=11, joypad=0x00)),
        NativeSpecFrameWitness(102, 0x00, _ram(smb_frame=12, timer=7, joypad=0x00)),
        NativeSpecFrameWitness(103, 0x42, _ram(smb_frame=13, timer=6, joypad=0x42)),
        NativeSpecFrameWitness(104, 0x42, _ram(smb_frame=14, timer=5, joypad=0x42)),
    )
    runner = _FakeRunner(witnesses)

    outcome = v36._evaluate_native_reward_chunk(
        runner,
        REWARD_BEAM_CHUNKS_WITH_HOLD[5],
        root_observation=root_observation,
        root_radar=root_radar,
        target_type="star",
        request_radar=root_radar,
    )

    assert outcome["frames"] == 2
    assert outcome["died"] is False
    assert outcome["collected"] is True
    assert outcome["observation"].native_frame_id == 102


def test_native_reward_evaluator_decodes_full_radar_only_at_semantic_endpoint(monkeypatch):
    v36 = _load_v36()
    root_observation, root_radar = _root_context()
    witnesses = tuple(
        NativeSpecFrameWitness(
            101 + index,
            0x82,
            _ram(smb_frame=11 + index, joypad=0x82),
        )
        for index in range(4)
    )
    runner = _FakeRunner(witnesses)
    calls = []
    original = v36.decode_smb1_radar

    def counted(ram, *, player_x, **kwargs):
        calls.append((ram, int(player_x)))
        return original(ram, player_x=player_x, **kwargs)

    monkeypatch.setattr(v36, "decode_smb1_radar", counted)
    outcome = v36._evaluate_native_reward_chunk(
        runner,
        REWARD_BEAM_CHUNKS_WITH_HOLD[1],
        root_observation=root_observation,
        root_radar=root_radar,
        target_type="star",
        request_radar=root_radar,
    )

    assert outcome["frames"] == 4
    assert outcome["collected"] is False
    assert len(calls) == 1
    assert calls[0][0] == witnesses[-1].ram


def test_v36_falls_back_to_v35_exact_live_core_path_on_native_failure(monkeypatch):
    v36 = _load_v36()
    sentinel = {"candidate": "legacy-v35-fallback"}
    released = []

    v36.v35._LIVE_AUTHORITY_CORE = object()
    monkeypatch.setattr(
        v36,
        "_sync_native_star_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(MesenLoadError("native fail")),
    )
    monkeypatch.setattr(v36, "_release_native_spec_runner", lambda: released.append(True))
    monkeypatch.setattr(v36.v35.v11, "_log", lambda message: None)
    monkeypatch.setattr(
        v36.v35,
        "_best_collect_or_progress",
        lambda *args, **kwargs: sentinel,
    )

    result = v36._best_collect_or_progress(
        [],
        current_frame=123,
        freshness=0,
        last_applied_generation=-1,
        live_radar={"collect_target_type": "star"},
    )

    assert result is sentinel
    assert released == [True]
    assert v36.v35.v23._latest_forward_meta["forward_model_status"] == (
        "native-sync-star-fallback"
    )


def test_v36_installs_native_selector_above_v35_composition():
    v36 = _load_v36()
    v36._install_v36_overrides()

    assert v36.v35.v26._LOWER_PLAN_DELEGATE.selector is v36._best_collect_or_progress
    assert v36.v35.v23.authority_main is v36.authority_main
    assert v36.v35.v23.PLANNER_NAME == v36.PLANNER_NAME
