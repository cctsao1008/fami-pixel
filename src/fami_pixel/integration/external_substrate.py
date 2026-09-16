from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from fami_pixel.adapters.mesen import MesenCore
from fami_pixel.adapters.mesen.fami_pixel_input import set_nes_controller_state
from fami_pixel.games.smb1.observation import Smb1Observation, read_smb1_observation


@dataclass(frozen=True)
class FamiObservation:
    observation_id: str
    source_identity: str
    native_frame_id: int
    smb1: Smb1Observation
    status: str = "OBSERVED"
    schema_version: str = "fami.external.observation.v1"

    def __post_init__(self) -> None:
        if not self.observation_id or not self.source_identity:
            raise ValueError("observation identity and source identity are required")
        if self.native_frame_id < 0:
            raise ValueError("native_frame_id must be non-negative")


@dataclass(frozen=True)
class CommandChunk:
    buttons: int
    frames: int

    def __post_init__(self) -> None:
        if not 0 <= self.buttons <= 0xFF:
            raise ValueError("buttons must fit in one NES controller byte")
        if self.frames <= 0:
            raise ValueError("frames must be positive")


@dataclass(frozen=True)
class BoundedAction:
    action_id: str
    source_observation_id: str
    chunks: tuple[CommandChunk, ...]
    frame_bound: int
    metadata: Mapping[str, object] | None = None
    schema_version: str = "fami.external.bounded-action.v1"

    def __post_init__(self) -> None:
        if not self.action_id or not self.source_observation_id:
            raise ValueError("action identity and source observation identity are required")
        if self.frame_bound <= 0:
            raise ValueError("frame_bound must be positive")
        if not self.chunks:
            raise ValueError("bounded action requires at least one command chunk")
        total = sum(chunk.frames for chunk in self.chunks)
        if total > self.frame_bound:
            raise ValueError("command chunks exceed declared frame_bound")


@dataclass(frozen=True)
class ShadowRollout:
    rollout_id: str
    source_identity: str
    source_state_identity: str
    action_id: str
    terminal_state_identity: str
    frames_simulated: int
    outcome: Mapping[str, object]
    evidence_kind: str = "SHADOW_ROLLOUT"
    schema_version: str = "fami.external.shadow-rollout.v1"

    def __post_init__(self) -> None:
        if self.evidence_kind != "SHADOW_ROLLOUT":
            raise ValueError("ShadowRollout evidence_kind is fixed")
        if self.frames_simulated < 0:
            raise ValueError("frames_simulated must be non-negative")


@dataclass(frozen=True)
class LiveExecutionTrace:
    execution_id: str
    source_identity: str
    action_id: str
    pre_observation: FamiObservation
    post_observation: FamiObservation
    requested_frames: int
    realized_frames: int
    chunks: tuple[CommandChunk, ...]
    evidence_kind: str = "LIVE_EXECUTION"
    schema_version: str = "fami.external.live-execution.v1"

    def __post_init__(self) -> None:
        if self.evidence_kind != "LIVE_EXECUTION":
            raise ValueError("LiveExecutionTrace evidence_kind is fixed")
        if self.requested_frames <= 0:
            raise ValueError("requested_frames must be positive")
        if self.realized_frames < 0:
            raise ValueError("realized_frames must be non-negative")


@dataclass(frozen=True)
class LiveConsequence:
    consequence_id: str
    source_identity: str
    execution_id: str
    pre_state_identity: str
    post_state_identity: str
    post_observation: FamiObservation
    status: Mapping[str, object]
    evidence_kind: str = "LIVE_CONSEQUENCE"
    schema_version: str = "fami.external.live-consequence.v1"

    def __post_init__(self) -> None:
        if self.evidence_kind != "LIVE_CONSEQUENCE":
            raise ValueError("LiveConsequence evidence_kind is fixed")
        if not self.execution_id:
            raise ValueError("execution_id is required")


ActionProposer = Callable[[FamiObservation], Sequence[BoundedAction]]
RolloutProvider = Callable[[BoundedAction], ShadowRollout]
ObservationReader = Callable[[MesenCore, int], Smb1Observation]
ButtonWriter = Callable[[MesenCore, int, int], None]


class Smb1ExternalSubstratePort:
    """Stable Fami-native port for external orchestration.

    This facade exposes observation, bounded action proposal, shadow rollout,
    bounded live execution, and realized consequence records. It intentionally
    contains no LSMM semantic or policy types.
    """

    def __init__(
        self,
        core: MesenCore,
        *,
        source_identity: str,
        controller_port: int = 0,
        timeout_ms: int = 2000,
        action_proposer: ActionProposer | None = None,
        rollout_provider: RolloutProvider | None = None,
        observation_reader: ObservationReader = read_smb1_observation,
        button_writer: ButtonWriter = set_nes_controller_state,
    ) -> None:
        if not source_identity:
            raise ValueError("source_identity is required")
        if controller_port not in (0, 1):
            raise ValueError("controller_port must be 0 or 1")
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        self._core = core
        self._source_identity = source_identity
        self._controller_port = controller_port
        self._timeout_ms = timeout_ms
        self._action_proposer = action_proposer
        self._rollout_provider = rollout_provider
        self._observation_reader = observation_reader
        self._button_writer = button_writer
        self._last_observation: FamiObservation | None = None

    def observe(self) -> FamiObservation:
        value = self._read_observation()
        self._last_observation = value
        return value

    def propose_actions(self, observation: FamiObservation) -> tuple[BoundedAction, ...]:
        if self._action_proposer is None:
            return ()
        values = tuple(self._action_proposer(observation))
        if not all(isinstance(value, BoundedAction) for value in values):
            raise TypeError("action proposer must return BoundedAction records")
        for value in values:
            if value.source_observation_id != observation.observation_id:
                raise ValueError("action candidate does not bind the source observation")
        return values

    def rollout_bounded(self, action: BoundedAction) -> ShadowRollout:
        if self._rollout_provider is None:
            raise RuntimeError("no shadow rollout provider is configured")
        value = self._rollout_provider(action)
        if not isinstance(value, ShadowRollout):
            raise TypeError("rollout provider must return ShadowRollout")
        if value.action_id != action.action_id:
            raise ValueError("rollout does not bind the requested action")
        return value

    def execute_bounded(self, action: BoundedAction) -> LiveExecutionTrace:
        pre = self._last_observation
        if pre is None:
            raise RuntimeError("observe() must freeze a live pre-state before execution")
        if action.source_observation_id != pre.observation_id:
            raise ValueError("bounded action does not bind the frozen live pre-state")

        authoritative_frame = int(self._core.frame_count())
        if authoritative_frame != pre.native_frame_id:
            raise ValueError("live state advanced after the action source observation")

        start_frame = pre.native_frame_id
        requested = sum(chunk.frames for chunk in action.chunks)
        for chunk in action.chunks:
            self._button_writer(self._core, self._controller_port, chunk.buttons)
            self._core.step_frame_sync(chunk.frames, self._timeout_ms)

        # Release after the bounded command without advancing another frame.
        self._button_writer(self._core, self._controller_port, 0)
        post = self._read_observation()
        self._last_observation = post
        realized = post.native_frame_id - start_frame
        if realized < 0:
            raise RuntimeError("authoritative frame counter moved backwards")

        return LiveExecutionTrace(
            execution_id=f"fami-execution:{uuid4()}",
            source_identity=self._source_identity,
            action_id=action.action_id,
            pre_observation=pre,
            post_observation=post,
            requested_frames=requested,
            realized_frames=realized,
            chunks=action.chunks,
        )

    def collect_consequence(self, trace: LiveExecutionTrace) -> LiveConsequence:
        if trace.source_identity != self._source_identity:
            raise ValueError("execution trace source identity mismatch")
        pre_id = self._state_identity(trace.pre_observation)
        post_id = self._state_identity(trace.post_observation)
        return LiveConsequence(
            consequence_id=f"fami-consequence:{uuid4()}",
            source_identity=self._source_identity,
            execution_id=trace.execution_id,
            pre_state_identity=pre_id,
            post_state_identity=post_id,
            post_observation=trace.post_observation,
            status={
                "requested_frames": trace.requested_frames,
                "realized_frames": trace.realized_frames,
            },
        )

    def _read_observation(self) -> FamiObservation:
        frame = int(self._core.frame_count())
        smb1 = self._observation_reader(self._core, frame)
        return FamiObservation(
            observation_id=f"fami-observation:{frame}:{uuid4()}",
            source_identity=self._source_identity,
            native_frame_id=frame,
            smb1=smb1,
        )

    @staticmethod
    def _state_identity(observation: FamiObservation) -> str:
        state = observation.smb1
        return (
            f"frame={observation.native_frame_id};"
            f"world={state.world};level={state.level};"
            f"x={state.mario_x_abs};y={state.mario_y};"
            f"mode={state.oper_mode};engine={state.game_engine_subroutine}"
        )
