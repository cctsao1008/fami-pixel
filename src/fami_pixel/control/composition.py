"""Stable dependency composition for current authority control.

The historical planner chain still supplies several concrete implementations while
#35 is being migrated.  This module makes those dependencies explicit and binds
them behind stable control objects, so current selectors do not reach through
versioned modules for cache wiring, worker coverage, proof horizons, objective
detection, PROGRESS fallback, or lineage state during every decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from fami_pixel.planning.progress_selection import ProgressSelection, select_progress_proof

from .eager_collect import CollectResponseCache, EagerCollectDecision, select_eager_collect_decision
from .progress import ProgressResponseCache, evaluated_anchor_set, lineage_valid_proofs
from .response_intake import ResponseReader, read_available_responses


CacheProvider = Callable[[], CollectResponseCache]
ActiveWorkerSelector = Callable[[int], Iterable[int]]
ProofHorizonSelector = Callable[[int], int]
CollectTargetSelector = Callable[[Mapping], str | None]


@dataclass(frozen=True)
class EagerCollectControl:
    """Explicit dependencies for one asynchronous eager-COLLECT authority scan.

    The object owns no emulator state and performs no candidate search.  It only
    composes stable response intake with the already-extracted eager-COLLECT
    orchestration.  Concrete cache/proof/lineage implementations are injected so
    historical compatibility can remain while the current path stops discovering
    those dependencies through transitive module globals.
    """

    response_reader: ResponseReader
    cache_provider: CacheProvider
    handoff_frames: tuple[int, ...]
    active_workers: ActiveWorkerSelector
    proof_selector: Any
    ledger: Any
    proof_horizon_frames: ProofHorizonSelector
    commit_frames: int

    def decide(
        self,
        response_paths: Sequence[Path],
        *,
        current_frame: int,
        freshness: int,
        last_applied_generation: int,
        target_type: str,
        live_radar: Mapping | None,
    ) -> EagerCollectDecision:
        """Read visible responses and run the stable eager-COLLECT authority scan."""

        fresh = int(freshness)
        responses = read_available_responses(
            response_paths,
            reader=self.response_reader,
        )
        retention = max(fresh, int(self.proof_horizon_frames(fresh)))
        return select_eager_collect_decision(
            responses,
            cache=self.cache_provider(),
            handoff_frames=self.handoff_frames,
            current_frame=int(current_frame),
            last_applied_generation=int(last_applied_generation),
            target_type=str(target_type),
            live_radar=live_radar,
            retention_frames=retention,
            active_workers=self.active_workers(len(response_paths)),
            proof_selector=self.proof_selector,
            ledger=self.ledger,
            commit_frames=int(self.commit_frames),
        )


@dataclass(frozen=True)
class ProgressControl:
    """Stable PROGRESS response intake, lineage validation, and cohort policy."""

    response_reader: ResponseReader
    cache: ProgressResponseCache
    required_anchors: frozenset[str]
    ledger: Any
    commit_frames: int
    live_horizon_frames: int

    def decide(
        self,
        response_paths: Sequence[Path],
        *,
        current_frame: int,
        freshness: int,
        last_applied_generation: int,
        live_radar: Mapping | None,
    ) -> ProgressSelection:
        radar = {} if live_radar is None else live_radar
        responses = read_available_responses(
            response_paths,
            reader=self.response_reader,
        )
        self.cache.ingest(
            responses,
            current_frame=int(current_frame),
            freshness=int(freshness),
            last_applied_generation=int(last_applied_generation),
            live_horizon_frames=int(self.live_horizon_frames),
        )

        def anchors(proofs: list[dict]) -> set[str]:
            return evaluated_anchor_set(
                proofs,
                required_anchors=self.required_anchors,
            )

        def validate(proofs: list[dict], *, current_frame: int):
            return lineage_valid_proofs(
                proofs,
                ledger=self.ledger,
                current_frame=int(current_frame),
                commit_frames=int(self.commit_frames),
            )

        return select_progress_proof(
            self.cache.groups(),
            current_frame=int(current_frame),
            required_anchors=self.required_anchors,
            evaluated_anchors=anchors,
            lineage_validator=validate,
            live_radar=radar,
        )


@dataclass(frozen=True)
class CollectProgressDecision:
    """Lower-objective result below SURVIVE authority."""

    plan: dict | None
    meta: dict | None
    target_type: str | None


@dataclass(frozen=True)
class CollectProgressControl:
    """Explicit lower-objective composition for COLLECT versus PROGRESS.

    This object does not own SURVIVE/preemption ordering. It binds stable reward
    target detection, stable PROGRESS orchestration, and stable eager-COLLECT
    control behind one dependency boundary.
    """

    target_selector: CollectTargetSelector
    progress: ProgressControl
    eager_collect: EagerCollectControl

    def target_type(self, live_radar: Mapping | None) -> str | None:
        radar = {} if live_radar is None else live_radar
        target = self.target_selector(radar)
        return None if target is None else str(target)

    def decide(
        self,
        response_paths: Sequence[Path],
        *,
        current_frame: int,
        freshness: int,
        last_applied_generation: int,
        target_type: str | None,
        live_radar: Mapping | None,
    ) -> CollectProgressDecision:
        """Choose stable PROGRESS or stable asynchronous COLLECT control."""

        radar = {} if live_radar is None else live_radar
        if target_type is None:
            decision = self.progress.decide(
                response_paths,
                current_frame=int(current_frame),
                freshness=int(freshness),
                last_applied_generation=int(last_applied_generation),
                live_radar=radar,
            )
            return CollectProgressDecision(
                plan=decision.plan,
                meta=decision.meta,
                target_type=None,
            )

        target = str(target_type)
        decision = self.eager_collect.decide(
            response_paths,
            current_frame=int(current_frame),
            freshness=int(freshness),
            last_applied_generation=int(last_applied_generation),
            target_type=target,
            live_radar=radar,
        )
        return CollectProgressDecision(
            plan=decision.plan,
            meta=decision.meta,
            target_type=target,
        )
