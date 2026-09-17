"""Per-handoff deadline admission for eager asynchronous COLLECT.

A worker response may be updated several times for one generation as progressively
later reward handoffs finish computing.  Worker-level first-seen timing is not
enough: if a worker publishes a +4f proof at age 3 and appends a +8f proof at age
9, the +8f proof must not inherit the worker's earlier arrival time.

This cache therefore stamps every branch proof independently using
``(generation, root, worker, candidate)``.  A reward branch is admitted only when
it is first observed no later than its own scheduled handoff.  Once admitted it
may remain in the cohort; ordinary action-lineage validation decides whether the
live authority actually followed it after the handoff.

The continuation anchor is treated like any other proof and normally has a
handoff at the proof horizon, so it remains available as a safe warm-start.
"""

from __future__ import annotations

from .collect_deadline import DeadlineCollectResponseCache


class HandoffDeadlineCollectResponseCache(DeadlineCollectResponseCache):
    """Deadline cache with irreversible branch-level handoff admission."""

    def __init__(self, *, deadline_frames: int) -> None:
        super().__init__(deadline_frames=deadline_frames)
        self._proof_first_seen_frame: dict[tuple[int, int, int, str], int] = {}
        self._proof_handoff_frames: dict[tuple[int, int, int, str], int] = {}
        self._late_proof_age: dict[tuple[int, int, int, str], int] = {}

    def clear(self) -> None:
        super().clear()
        self._proof_first_seen_frame.clear()
        self._proof_handoff_frames.clear()
        self._late_proof_age.clear()

    @staticmethod
    def _proof_identity(response: dict, proof: dict) -> tuple[int, int, int, str] | None:
        try:
            generation = int(proof.get("generation", response.get("generation", -1)))
            root_frame = int(proof.get("root_frame", response.get("root_frame", -1)))
            worker = int(proof.get("worker", response.get("worker", -1)))
            candidate = str(proof.get("candidate") or "")
        except (TypeError, ValueError):
            return None
        if generation < 0 or root_frame < 0 or worker < 0 or not candidate:
            return None
        return generation, root_frame, worker, candidate

    @staticmethod
    def _proof_items(response: dict) -> list[dict]:
        raw = response.get("branch_proofs")
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, dict)]
        if response.get("candidate"):
            return [dict(response)]
        return []

    def ingest(
        self,
        responses: list[dict] | tuple[dict, ...],
        *,
        current_frame: int,
        last_applied_generation: int,
        retention_frames: int,
        target_type: str,
    ) -> None:
        sanitized: list[dict] = []
        now = int(current_frame)

        for response in responses:
            if not response or "error" in response:
                continue
            if str(response.get("planner_mode")) != "collect":
                continue
            if str(response.get("target_reward_type")) != str(target_type):
                continue

            accepted_proofs: list[dict] = []
            for proof in self._proof_items(response):
                key = self._proof_identity(response, proof)
                if key is None:
                    continue
                generation, root_frame, worker, candidate = key
                proof.setdefault("generation", generation)
                proof.setdefault("root_frame", root_frame)
                proof.setdefault("worker", worker)
                proof.setdefault("planner_mode", "collect")
                proof.setdefault("target_reward_type", target_type)
                try:
                    handoff = int(proof.get("collect_handoff_frames", 0))
                except (TypeError, ValueError):
                    continue
                if handoff < 0:
                    continue

                if key not in self._proof_first_seen_frame:
                    self._proof_first_seen_frame[key] = now
                    self._proof_handoff_frames[key] = handoff
                first_seen = int(self._proof_first_seen_frame[key])
                arrival_age = first_seen - int(root_frame)
                if arrival_age > handoff:
                    self._late_proof_age[key] = int(arrival_age)
                    continue
                accepted_proofs.append(proof)

            copy = dict(response)
            copy["branch_proofs"] = accepted_proofs
            if not accepted_proofs:
                # Preserve worker/cohort coverage without allowing a late top-level
                # candidate to leak through flatten_collect_proofs() fallback.
                copy["candidate"] = f"collect_worker_{copy.get('worker', -1)}_handoff_coverage"
                copy["trajectory_event"] = "coverage_only"
                copy["reward_prefix_safe"] = False
                copy["trajectory_frames"] = 0
                copy["schedule"] = [{"buttons": 0, "frames": 1}]
            sanitized.append(copy)

        super().ingest(
            sanitized,
            current_frame=current_frame,
            last_applied_generation=last_applied_generation,
            retention_frames=retention_frames,
            target_type=target_type,
        )

        # Retire branch timing with the same lifecycle as cohort evidence.
        for key in list(self._proof_first_seen_frame):
            generation, root_frame, _worker, _candidate = key
            age = now - int(root_frame)
            if (
                generation <= int(last_applied_generation)
                or age < 0
                or age > int(retention_frames)
            ):
                self._proof_first_seen_frame.pop(key, None)
                self._proof_handoff_frames.pop(key, None)
                self._late_proof_age.pop(key, None)

    def proof_arrival_age(
        self,
        *,
        generation: int,
        root_frame: int,
        worker: int,
        candidate: str,
    ) -> int | None:
        key = (int(generation), int(root_frame), int(worker), str(candidate))
        seen = self._proof_first_seen_frame.get(key)
        if seen is None:
            return None
        return int(seen) - int(root_frame)

    def handoff_snapshot(self, *, generation: int, root_frame: int) -> dict:
        """Return branch-level arrival/deadline evidence for one cohort."""

        generation = int(generation)
        root_frame = int(root_frame)
        rows: list[dict] = []
        for key, seen in self._proof_first_seen_frame.items():
            key_generation, key_root, worker, candidate = key
            if key_generation != generation or key_root != root_frame:
                continue
            handoff = int(self._proof_handoff_frames.get(key, 0))
            arrival_age = int(seen) - root_frame
            rows.append(
                {
                    "worker": int(worker),
                    "candidate": str(candidate),
                    "handoff_frames": handoff,
                    "arrival_age_frames": arrival_age,
                    "deadline_slack_frames": handoff - arrival_age,
                    "admitted": arrival_age <= handoff,
                }
            )
        rows.sort(key=lambda row: (row["handoff_frames"], row["worker"], row["candidate"]))
        return {"proofs": rows}
