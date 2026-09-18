"""Stable authority-side checkpoint request enrichment.

The live authority loop owns *when* a checkpoint request is published. Planner
layers may annotate that request with control-plane context, but they should not
monkey-patch the IPC serializer itself. This module provides one explicit,
process-local enrichment seam for that purpose.

The default enricher is identity, so historical runners that do not install an
enricher retain their existing request payloads exactly.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, Mapping

from .authority_plan import AuthorityPlanMemory, ScheduleProjector


RequestPayloadEnricher = Callable[[Mapping], Mapping]


def _identity_enricher(payload: Mapping) -> dict:
    return dict(payload)


class RequestPayloadEnricherSlot:
    """Explicit process-local hook for one authority checkpoint request stream."""

    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._enricher: RequestPayloadEnricher = _identity_enricher

    @property
    def enricher(self) -> RequestPayloadEnricher:
        return self._enricher

    def install(self, enricher: RequestPayloadEnricher) -> None:
        if not callable(enricher):
            raise TypeError("request payload enricher must be callable")
        self._enricher = enricher

    def reset(self) -> None:
        self._enricher = _identity_enricher

    def enrich(self, payload: Mapping) -> dict:
        result = self._enricher(dict(payload))
        if not isinstance(result, Mapping):
            raise TypeError("request payload enricher must return a mapping")
        return dict(result)

    @contextmanager
    def installed(self, enricher: RequestPayloadEnricher) -> Iterator["RequestPayloadEnricherSlot"]:
        """Install one enricher for a bounded authority scope, then restore it."""

        previous = self._enricher
        self.install(enricher)
        try:
            yield self
        finally:
            self._enricher = previous


class AuthorityContinuationRequestEnricher:
    """Attach exact current-plan continuation context to checkpoint requests."""

    def __init__(
        self,
        memory: AuthorityPlanMemory,
        *,
        proof_horizon: int,
        projector: ScheduleProjector,
    ) -> None:
        if int(proof_horizon) <= 0:
            raise ValueError("proof_horizon must be > 0")
        self._memory = memory
        self._proof_horizon = int(proof_horizon)
        self._projector = projector

    def __call__(self, payload: Mapping) -> dict:
        data = dict(payload)
        try:
            frame = int(data["frame"])
        except (KeyError, TypeError, ValueError):
            return data

        continuation = self._memory.continuation(
            frame,
            frames=self._proof_horizon,
            projector=self._projector,
        )
        if not continuation:
            return data

        snapshot = self._memory.snapshot
        data["authority_continuation_schedule"] = continuation
        data["authority_continuation_candidate"] = (
            None if snapshot is None else snapshot.get("candidate")
        )
        data["authority_continuation_root_frame"] = (
            None if snapshot is None else snapshot.get("root_frame")
        )
        return data


_CHECKPOINT_REQUEST_ENRICHER = RequestPayloadEnricherSlot("checkpoint-request")


def enrich_checkpoint_request(payload: Mapping) -> dict:
    """Apply the currently installed checkpoint-request annotation policy."""

    return _CHECKPOINT_REQUEST_ENRICHER.enrich(payload)


@contextmanager
def installed_checkpoint_request_enricher(
    enricher: RequestPayloadEnricher,
) -> Iterator[RequestPayloadEnricherSlot]:
    """Install an authority request enricher for one bounded control scope."""

    with _CHECKPOINT_REQUEST_ENRICHER.installed(enricher) as slot:
        yield slot
