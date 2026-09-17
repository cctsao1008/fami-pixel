from __future__ import annotations

from pathlib import Path

import pytest

from fami_pixel.games.smb1.observation import Smb1Observation
from fami_pixel.games.smb1.radar import Smb1RadarSnapshot
from fami_pixel.integration import (
    BoundedAction,
    CommandChunk,
    LiveConsequence,
    ShadowRollout,
    Smb1ExternalSubstratePort,
)


class FakeCore:
    def __init__(self) -> None:
        self.frame = 100
        self.steps: list[tuple[int, int]] = []

    def frame_count(self) -> int:
        return self.frame

    def step_frame_sync(self, count: int, timeout_ms: int) -> None:
        self.steps.append((count, timeout_ms))
        self.frame += count


def fake_observer(core: FakeCore, frame: int) -> Smb1Observation:
    return Smb1Observation(
        native_frame_id=frame,
        smb_frame_counter=frame & 0xFF,
        world=0,
        level=0,
        mario_x_abs=frame,
        mario_y=80,
        mario_y_high=0,
        player_state=0,
        player_x_speed=1,
        player_y_speed=0,
        raw_joypad=0,
        oper_mode=1,
        oper_mode_task=0,
        game_engine_subroutine=8,
    )


def radar_snapshot(player_x: int) -> Smb1RadarSnapshot:
    return Smb1RadarSnapshot(
        player_x=player_x,
        lookahead_px=96,
        screen_right_x=player_x + 96,
        terrain_valid_through_x=player_x + 96,
        enemies=(),
        rewards=(),
        columns=(),
        nearest_enemy_dx=None,
        nearest_gap_dx=None,
        nearest_obstacle_dx=None,
        nearest_reward_dx=None,
        nearest_reward_type=None,
        reference_surface_row=12,
        player_status=0,
        star_invincible_timer=0,
    )


def make_port(*, rollout_provider=None, action_proposer=None):
    core = FakeCore()
    writes: list[tuple[int, int]] = []
    radar_reads: list[int] = []

    def writer(core, port, buttons):
        writes.append((port, buttons))

    def radar_reader(core, player_x):
        radar_reads.append(player_x)
        return radar_snapshot(player_x)

    port = Smb1ExternalSubstratePort(
        core,
        source_identity="mesen:test-commit",
        timeout_ms=25,
        observation_reader=fake_observer,
        radar_reader=radar_reader,
        button_writer=writer,
        rollout_provider=rollout_provider,
        action_proposer=action_proposer,
    )
    return core, writes, radar_reads, port


def bounded_action(observation_id: str) -> BoundedAction:
    return BoundedAction(
        action_id="action-1",
        source_observation_id=observation_id,
        chunks=(CommandChunk(buttons=0x81, frames=2), CommandChunk(buttons=0x80, frames=1)),
        frame_bound=3,
    )


def test_f1_observation_carries_frame_source_and_radar_identity():
    core, writes, radar_reads, port = make_port()
    observation = port.observe()
    assert observation.native_frame_id == 100
    assert observation.source_identity == "mesen:test-commit"
    assert observation.smb1.native_frame_id == 100
    assert observation.radar.player_x == observation.smb1.mario_x_abs == 100
    assert radar_reads == [100]
    assert observation.status == "OBSERVED"
    assert observation.schema_version == "fami.external.observation.v2"


def test_f2_bounded_action_requires_bound_and_frozen_live_source():
    with pytest.raises(ValueError):
        BoundedAction(
            action_id="bad",
            source_observation_id="obs",
            chunks=(CommandChunk(buttons=0, frames=2),),
            frame_bound=1,
        )

    core, writes, radar_reads, port = make_port()
    observation = port.observe()
    action = bounded_action(observation.observation_id)
    core.frame += 1
    with pytest.raises(ValueError, match="advanced"):
        port.execute_bounded(action)


def test_f3_shadow_rollout_cannot_be_confused_with_live_consequence():
    core, writes, radar_reads, port = make_port()
    observation = port.observe()
    action = bounded_action(observation.observation_id)
    shadow = ShadowRollout(
        rollout_id="shadow-1",
        source_identity="mesen:test-commit",
        source_state_identity="state-pre",
        action_id=action.action_id,
        terminal_state_identity="state-shadow",
        frames_simulated=3,
        outcome={"survived": True},
    )
    assert shadow.evidence_kind == "SHADOW_ROLLOUT"
    assert "shadow" in shadow.schema_version
    assert not isinstance(shadow, LiveConsequence)


def test_f4_live_consequence_binds_execution_and_refreshes_post_radar():
    core, writes, radar_reads, port = make_port()
    observation = port.observe()
    action = bounded_action(observation.observation_id)
    trace = port.execute_bounded(action)
    consequence = port.collect_consequence(trace)

    assert trace.requested_frames == 3
    assert trace.realized_frames == 3
    assert trace.pre_observation.observation_id == observation.observation_id
    assert trace.post_observation.native_frame_id == 103
    assert trace.pre_observation.radar.player_x == 100
    assert trace.post_observation.radar.player_x == 103
    assert radar_reads == [100, 103]
    assert consequence.execution_id == trace.execution_id
    assert consequence.post_observation.radar.player_x == 103
    assert "frame=103" in consequence.post_state_identity
    assert consequence.evidence_kind == "LIVE_CONSEQUENCE"
    assert core.steps == [(2, 25), (1, 25)]
    assert writes[-1] == (0, 0)  # explicit release without extra frame


def test_f5_action_proposal_and_rollout_use_existing_port_without_semantic_authority():
    captured: dict[str, str] = {}

    def proposer(observation):
        captured["observation"] = observation.observation_id
        return (bounded_action(observation.observation_id),)

    def rollout(action):
        return ShadowRollout(
            rollout_id="shadow-1",
            source_identity="mesen:test-commit",
            source_state_identity="state-pre",
            action_id=action.action_id,
            terminal_state_identity="state-shadow",
            frames_simulated=3,
            outcome={"survived": True},
        )

    core, writes, radar_reads, port = make_port(
        action_proposer=proposer, rollout_provider=rollout
    )
    observation = port.observe()
    actions = port.propose_actions(observation)
    shadow = port.rollout_bounded(actions[0])

    assert captured["observation"] == observation.observation_id
    assert shadow.action_id == actions[0].action_id


def test_f6_observation_rejects_radar_player_identity_mismatch():
    core = FakeCore()

    def bad_radar_reader(core, player_x):
        return radar_snapshot(player_x + 1)

    port = Smb1ExternalSubstratePort(
        core,
        source_identity="mesen:test-commit",
        observation_reader=fake_observer,
        radar_reader=bad_radar_reader,
        button_writer=lambda core, port, buttons: None,
    )
    with pytest.raises(ValueError, match="radar player_x"):
        port.observe()


def test_f7_observation_fails_if_authoritative_frame_advances_during_sensing():
    core = FakeCore()

    def advancing_radar_reader(core, player_x):
        core.frame += 1
        return radar_snapshot(player_x)

    port = Smb1ExternalSubstratePort(
        core,
        source_identity="mesen:test-commit",
        observation_reader=fake_observer,
        radar_reader=advancing_radar_reader,
        button_writer=lambda core, port, buttons: None,
    )
    with pytest.raises(RuntimeError, match="frame advanced"):
        port.observe()


def test_f8_integration_module_has_no_lsmm_dependency_import():
    module = Path(__file__).resolve().parents[1] / "src" / "fami_pixel" / "integration" / "external_substrate.py"
    text = module.read_text(encoding="utf-8")
    import_lines = [
        line.strip().lower()
        for line in text.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    ]
    assert not any("lsmm" in line for line in import_lines)
