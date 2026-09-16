"""Bounded candidate generation and learned rank/prune for SMB1 trajectory search.

Mesen remains the branch authority.  This module only decides which bounded
candidate prefixes are worth spending exact emulator rollouts on.

The trained tiny surrogate was learned from short rollout prefixes and exposes
predicted delta-X, risk, and no-progress heads.  Search therefore uses the model
as a *cheap ordering heuristic*, never as a safety proof:

    bounded candidate expansion
        -> surrogate ordering / top-K pruning
        -> exact Mesen event-horizon evaluation

Known-good baseline trajectories are kept as diversity anchors so an OOD learned
score cannot prune every established maneuver.  Multi-chunk candidates are
allowed to contain more commands than the surrogate's historical two-command
feature window; in that case the model is explicitly only ranking the visible
prefix.  Exact Mesen evaluates the complete plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Protocol

from .actions import ActionCommand, Smb1Action
from .forward_model import BASELINE_TRAJECTORY_PLANS
from .observation import Smb1Observation
from .trajectory import TrajectoryPlan


DEFAULT_SEARCH_DEPTH = 3
DEFAULT_TOP_K = 12
MAX_PREFIX_FRAMES = 28


@dataclass(frozen=True)
class TrajectoryChunk:
    name: str
    commands: tuple[ActionCommand, ...]
    tail_action: Smb1Action


SEARCH_CHUNKS: tuple[TrajectoryChunk, ...] = (
    TrajectoryChunk(
        "run4",
        (ActionCommand(Smb1Action.RIGHT_B, 4),),
        Smb1Action.RIGHT_B,
    ),
    TrajectoryChunk(
        "coast2",
        (ActionCommand(Smb1Action.NOOP, 2),),
        Smb1Action.NOOP,
    ),
    TrajectoryChunk(
        "brake4",
        (ActionCommand(Smb1Action.LEFT_B, 4),),
        Smb1Action.NOOP,
    ),
    TrajectoryChunk(
        "hold_right_jump4",
        (ActionCommand(Smb1Action.RIGHT_A_B, 4),),
        Smb1Action.RIGHT_B,
    ),
    TrajectoryChunk(
        "hold_left_jump4",
        (ActionCommand(Smb1Action.LEFT_A_B, 4),),
        Smb1Action.LEFT_B,
    ),
    TrajectoryChunk(
        "rearm_short",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 7),
        ),
        Smb1Action.RIGHT_B,
    ),
    TrajectoryChunk(
        "rearm_long",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 15),
        ),
        Smb1Action.RIGHT_B,
    ),
)

# These anchors preserve established maneuver diversity.  They consume part of
# the exact rollout budget but are not allowed to disappear because the learned
# model dislikes an OOD state.
DEFAULT_ANCHOR_NAMES = (
    "fm_long_jump",
    "fm_brake_jump",
    "fm_run",
    "fm_backtrack",
)


class SurrogateModel(Protocol):
    def predict(self, record: dict) -> dict[str, float]: ...


@dataclass(frozen=True)
class RankedTrajectory:
    plan: TrajectoryPlan
    predicted_delta_x: float
    risk_probability: float
    no_progress_probability: float
    rank_key: tuple[float, float, float, str]
    anchored: bool = False


@dataclass(frozen=True)
class SearchFrontier:
    generated: int
    pruned: int
    depth: int
    top_k: int
    ranked: tuple[RankedTrajectory, ...]

    @property
    def plans(self) -> tuple[TrajectoryPlan, ...]:
        return tuple(item.plan for item in self.ranked)


def _signed_u8(value: int) -> int:
    raw = int(value) & 0xFF
    return raw - 0x100 if raw & 0x80 else raw


def _merge_commands(commands: tuple[ActionCommand, ...]) -> tuple[ActionCommand, ...]:
    merged: list[ActionCommand] = []
    for command in commands:
        if merged and merged[-1].action == command.action:
            previous = merged[-1]
            merged[-1] = ActionCommand(
                previous.action,
                int(previous.frame_count) + int(command.frame_count),
            )
        else:
            merged.append(command)
    return tuple(merged)


def _plan_signature(plan: TrajectoryPlan) -> tuple:
    return (
        tuple((command.action.value, int(command.frame_count)) for command in plan.commands),
        plan.tail_action.value,
    )


def generate_bounded_trajectory_plans(
    *,
    depth: int = DEFAULT_SEARCH_DEPTH,
    chunks: tuple[TrajectoryChunk, ...] = SEARCH_CHUNKS,
    include_baseline: bool = True,
    max_prefix_frames: int = MAX_PREFIX_FRAMES,
) -> tuple[TrajectoryPlan, ...]:
    """Generate a deterministic, deduplicated bounded multi-chunk vocabulary."""

    if depth <= 0:
        raise ValueError("depth must be > 0")
    if not chunks:
        raise ValueError("chunks must be non-empty")
    if max_prefix_frames <= 0:
        raise ValueError("max_prefix_frames must be > 0")

    plans: list[TrajectoryPlan] = []
    seen: set[tuple] = set()

    def append(plan: TrajectoryPlan) -> None:
        signature = _plan_signature(plan)
        if signature in seen:
            return
        if plan.prefix_frames > int(max_prefix_frames):
            return
        seen.add(signature)
        plans.append(plan)

    if include_baseline:
        for plan in BASELINE_TRAJECTORY_PLANS:
            append(plan)

    # Search depth counts composable chunks rather than raw ActionCommand count.
    # This allows, for example, brake -> coast -> rearm-long while keeping the
    # branching factor explicit and bounded.
    for chunk_depth in range(1, int(depth) + 1):
        for sequence in product(chunks, repeat=chunk_depth):
            commands = _merge_commands(
                tuple(command for chunk in sequence for command in chunk.commands)
            )
            plan = TrajectoryPlan(
                "beam_" + "__".join(chunk.name for chunk in sequence),
                commands,
                tail_action=sequence[-1].tail_action,
            )
            append(plan)

    return tuple(plans)


def surrogate_record_for_plan(
    observation: Smb1Observation,
    plan: TrajectoryPlan,
) -> dict:
    """Build the runtime feature record expected by ``TinySurrogateMLP``."""

    return {
        "start": {
            "x": int(observation.mario_x_abs),
            "y": int(observation.mario_y),
            "y_high": int(observation.mario_y_high),
            "vx": _signed_u8(observation.player_x_speed),
            "vy": _signed_u8(observation.player_y_speed),
            "player_state": int(observation.player_state),
            "engine": int(observation.game_engine_subroutine),
            "joypad": int(observation.raw_joypad),
        },
        "candidate": {
            "name": plan.name,
            "horizon_frames": int(plan.prefix_frames),
            "schedule": [
                {
                    "buttons": int(command.nes_buttons),
                    "frames": int(command.frame_count),
                }
                for command in plan.commands
            ],
        },
    }


def _prediction_key(prediction: dict[str, float], name: str) -> tuple[float, float, float, str]:
    """Progress-first learned ordering with soft risk/no-progress tie breakers.

    Risk calibration is intentionally *not* a hard veto here.  Exact Mesen is
    responsible for rejection.  This ordering primarily exploits the surrogate's
    useful delta-X ranking while preferring lower learned risk/stall when progress
    estimates are close.
    """

    return (
        float(prediction["delta_x"]),
        -float(prediction["risk_probability"]),
        -float(prediction["no_progress_probability"]),
        str(name),
    )


def rank_and_prune_trajectory_plans(
    observation: Smb1Observation,
    model: SurrogateModel,
    plans: tuple[TrajectoryPlan, ...],
    *,
    top_k: int = DEFAULT_TOP_K,
    anchor_names: tuple[str, ...] = DEFAULT_ANCHOR_NAMES,
    depth: int = DEFAULT_SEARCH_DEPTH,
) -> SearchFrontier:
    """Use the learned surrogate to choose a bounded exact-Mesen frontier."""

    if top_k <= 0:
        raise ValueError("top_k must be > 0")
    if not plans:
        raise ValueError("plans must be non-empty")

    ranked_all: list[RankedTrajectory] = []
    anchor_set = set(anchor_names)
    for plan in plans:
        prediction = model.predict(surrogate_record_for_plan(observation, plan))
        ranked_all.append(
            RankedTrajectory(
                plan=plan,
                predicted_delta_x=float(prediction["delta_x"]),
                risk_probability=float(prediction["risk_probability"]),
                no_progress_probability=float(prediction["no_progress_probability"]),
                rank_key=_prediction_key(prediction, plan.name),
                anchored=plan.name in anchor_set,
            )
        )

    ranked_all.sort(key=lambda item: item.rank_key, reverse=True)
    anchors = [item for item in ranked_all if item.anchored]
    if len(anchors) > int(top_k):
        raise ValueError("top_k is smaller than the required anchor set")

    selected: list[RankedTrajectory] = list(anchors)
    selected_names = {item.plan.name for item in selected}
    for item in ranked_all:
        if len(selected) >= int(top_k):
            break
        if item.plan.name in selected_names:
            continue
        selected.append(item)
        selected_names.add(item.plan.name)

    # Preserve learned order in the emitted frontier so deterministic sharding
    # distributes the best-ranked non-anchor prefixes first.
    selected.sort(key=lambda item: item.rank_key, reverse=True)
    return SearchFrontier(
        generated=len(plans),
        pruned=max(0, len(plans) - len(selected)),
        depth=int(depth),
        top_k=int(top_k),
        ranked=tuple(selected),
    )


def build_search_frontier(
    observation: Smb1Observation,
    model: SurrogateModel,
    *,
    depth: int = DEFAULT_SEARCH_DEPTH,
    top_k: int = DEFAULT_TOP_K,
) -> SearchFrontier:
    plans = generate_bounded_trajectory_plans(depth=depth)
    return rank_and_prune_trajectory_plans(
        observation,
        model,
        plans,
        top_k=top_k,
        depth=depth,
    )


def shard_ranked_frontier(
    frontier: SearchFrontier,
    worker_index: int,
    worker_count: int,
) -> tuple[RankedTrajectory, ...]:
    if worker_count <= 0:
        raise ValueError("worker_count must be > 0")
    if worker_index < 0 or worker_index >= worker_count:
        raise ValueError("worker_index must be within worker_count")
    shard = tuple(
        item
        for index, item in enumerate(frontier.ranked)
        if index % int(worker_count) == int(worker_index)
    )
    if shard:
        return shard
    return (frontier.ranked[int(worker_index) % len(frontier.ranked)],)
