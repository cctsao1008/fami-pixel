"""Cohort cache for delay-compensated asynchronous COLLECT workers.

Branch-level lineage validation prevents stale trajectories from describing the
wrong current state, but it does not by itself prevent a first-finisher race. If
one worker's valid continuation anchor arrives before another worker's stronger
reward branch and authority consumes the generation immediately, worker latency
still becomes policy.

This cache retains worker responses across response-file overwrites and exposes
root/generation groups so authority can require deterministic worker coverage
before objective ranking. Older complete cohorts may remain eligible only if the
separate action-lineage/proof-lease contract still validates them.
"""

from __future__ import annotations


class CollectResponseCache:
    def __init__(self) -> None:
        self._responses: dict[tuple[int, int, int], dict] = {}

    def clear(self) -> None:
        self._responses.clear()

    def ingest(
        self,
        responses: list[dict] | tuple[dict, ...],
        *,
        current_frame: int,
        last_applied_generation: int,
        retention_frames: int,
        target_type: str,
    ) -> None:
        if retention_frames < 0:
            raise ValueError("retention_frames must be >= 0")

        for response in responses:
            if not response or "error" in response:
                continue
            if str(response.get("planner_mode")) != "collect":
                continue
            if str(response.get("target_reward_type")) != str(target_type):
                continue
            try:
                generation = int(response.get("generation", -1))
                root_frame = int(response.get("root_frame", -1))
                worker = int(response.get("worker", -1))
            except (TypeError, ValueError):
                continue
            if generation < 0 or root_frame < 0 or worker < 0:
                continue
            self._responses[(generation, root_frame, worker)] = dict(response)

        for key, response in list(self._responses.items()):
            generation, root_frame, _worker = key
            age = int(current_frame) - root_frame
            if (
                generation <= int(last_applied_generation)
                or age < 0
                or age > int(retention_frames)
                or str(response.get("target_reward_type")) != str(target_type)
            ):
                del self._responses[key]

    def groups(self) -> dict[tuple[int, int], list[dict]]:
        """Return `(generation, root_frame) -> worker responses`."""

        grouped: dict[tuple[int, int], list[dict]] = {}
        for (generation, root_frame, _worker), response in self._responses.items():
            grouped.setdefault((generation, root_frame), []).append(dict(response))
        return grouped

    def ordered_groups(self) -> list[tuple[tuple[int, int], list[dict]]]:
        """Newest root/generation first, matching V27 PROGRESS cohort ordering."""

        groups = self.groups()
        return sorted(
            groups.items(),
            key=lambda item: (item[0][1], item[0][0]),
            reverse=True,
        )

    @staticmethod
    def worker_set(responses: list[dict] | tuple[dict, ...]) -> set[int]:
        workers: set[int] = set()
        for response in responses:
            try:
                workers.add(int(response.get("worker", -1)))
            except (TypeError, ValueError):
                continue
        workers.discard(-1)
        return workers

    @classmethod
    def complete(
        cls,
        responses: list[dict] | tuple[dict, ...],
        *,
        expected_workers: int,
    ) -> bool:
        if expected_workers <= 0:
            raise ValueError("expected_workers must be > 0")
        return len(cls.worker_set(responses)) >= int(expected_workers)

    def __len__(self) -> int:
        return len(self._responses)
