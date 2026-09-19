"""Stable PROGRESS response cache and lineage helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Collection, Iterable

from fami_pixel.games.smb1.action_lineage import validate_branch_proof


def response_branch_proofs(response: dict) -> list[dict]:
    raw = response.get("branch_proofs")
    if isinstance(raw, list) and raw:
        proofs: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            proof = dict(item)
            for key in ("generation", "worker", "root_frame", "planner_mode"):
                proof.setdefault(key, response.get(key))
            proofs.append(proof)
        if proofs:
            return proofs
    return [dict(response)]


@dataclass
class ProgressResponseCache:
    entries: dict[tuple[int, int, str], dict] = field(default_factory=dict)

    def clear(self) -> None:
        self.entries.clear()

    def ingest(
        self,
        responses: Iterable[dict],
        *,
        current_frame: int,
        freshness: int,
        last_applied_generation: int,
        live_horizon_frames: int,
    ) -> None:
        for response in responses:
            if response.get("planner_mode") != "progress-search":
                continue
            for proof in response_branch_proofs(response):
                try:
                    generation = int(proof.get("generation", -1))
                    root_frame = int(proof.get("root_frame", -1))
                    candidate = str(proof.get("candidate") or "")
                except (TypeError, ValueError):
                    continue
                if generation < 0 or root_frame < 0 or not candidate:
                    continue
                self.entries[(generation, root_frame, candidate)] = dict(proof)

        retention = max(int(freshness), int(live_horizon_frames))
        for key in list(self.entries):
            generation, root_frame, _candidate = key
            age = int(current_frame) - int(root_frame)
            if (
                generation <= int(last_applied_generation)
                or age < 0
                or age > retention
            ):
                del self.entries[key]

    def groups(self) -> dict[tuple[int, int], list[dict]]:
        groups: dict[tuple[int, int], list[dict]] = {}
        for (generation, root_frame, _candidate), proof in self.entries.items():
            groups.setdefault((generation, root_frame), []).append(dict(proof))
        return groups


def evaluated_anchor_set(
    proofs: list[dict],
    *,
    required_anchors: Collection[str],
) -> set[str]:
    required = frozenset(required_anchors)
    anchors: set[str] = set()
    for proof in proofs:
        candidate = str(proof.get("candidate") or "")
        if candidate in required:
            anchors.add(candidate)
        for value in proof.get("search_evaluated_anchors") or ():
            if str(value) in required:
                anchors.add(str(value))
    return anchors


def lineage_valid_proofs(
    proofs: list[dict],
    *,
    ledger,
    current_frame: int,
    commit_frames: int,
) -> tuple[list[dict], dict[str, int]]:
    valid: list[dict] = []
    rejected: dict[str, int] = {}
    for proof in proofs:
        validation = validate_branch_proof(
            proof,
            ledger=ledger,
            current_frame=current_frame,
            commit_frames=commit_frames,
        )
        if not validation.valid:
            rejected[validation.reason] = rejected.get(validation.reason, 0) + 1
            continue
        candidate = dict(proof)
        candidate["trajectory_source_age_frames"] = int(validation.source_age)
        candidate["lineage_matched_frames"] = int(validation.matched_frames)
        candidate["proof_remaining_frames"] = int(validation.proof_remaining_frames)
        candidate["lineage_validation"] = validation.reason
        valid.append(candidate)
    return valid, rejected
