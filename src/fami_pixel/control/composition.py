"""Stable dependency composition for current authority control.

The historical planner chain still supplies several concrete implementations while
#35 is being migrated.  This module makes those dependencies explicit and binds
them behind one stable control object, so current selectors do not reach through
versioned modules for cache wiring, worker coverage, proof horizons, or lineage
state during every decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .eager_collect import CollectResponseCache, EagerCollectDecision, select_eager_collect_decision
from .response_intake import ResponseReader, read_available_responses


CacheProvider = Callable[[], CollectResponseCache]
ActiveWorkerSelector = Callable[[int], Iterable[int]]
ProofHorizonSelector = Callable[[int], int]


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
