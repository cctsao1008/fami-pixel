"""Authority-observed deadline admission for asynchronous COLLECT cohorts.

A cohort deadline is only deterministic if membership stops changing after the
cutoff.  Merely checking ``current_age >= deadline`` is insufficient: a worker
first observed after the cutoff could otherwise enter the cached cohort on a
later control quantum and change ranking retroactively.

This cache extends :class:`CollectResponseCache` with one conservative rule:

    first authority observation age <= deadline  -> admitted
    first authority observation age >  deadline  -> permanently late/rejected

The observation frame is measured on the authoritative native-frame clock, not
worker wall time.  Worker ``compute_ms`` remains useful telemetry but never
certifies deadline admission.
"""

from __future__ import annotations

from .collect_cohort import CollectResponseCache


class DeadlineCollectResponseCache(CollectResponseCache):
    """COLLECT response cache with irreversible authority-frame admission."""

    def __init__(self, *, deadline_frames: int) -> None:
        super().__init__()
        if int(deadline_frames) < 0:
            raise ValueError("deadline_frames must be >= 0")
        self.deadline_frames = int(deadline_frames)
        self._first_seen_frame: dict[tuple[int, int, int], int] = {}
        self._target_type: dict[tuple[int, int, int], str] = {}
        self._late_arrival_age: dict[tuple[int, int, int], int] = {}
        self._worker_compute_ms: dict[tuple[int, int, int], float] = {}
        self._worker_exact_steps: dict[tuple[int, int, int], int] = {}

    def clear(self) -> None:
        super().clear()
        self._first_seen_frame.clear()
        self._target_type.clear()
        self._late_arrival_age.clear()
        self._worker_compute_ms.clear()
        self._worker_exact_steps.clear()

    @staticmethod
    def _identity(response: dict) -> tuple[int, int, int] | None:
        try:
            generation = int(response.get("generation", -1))
            root_frame = int(response.get("root_frame", -1))
            worker = int(response.get("worker", -1))
        except (TypeError, ValueError):
            return None
        if generation < 0 or root_frame < 0 or worker < 0:
            return None
        return generation, root_frame, worker

    def ingest(
        self,
        responses: list[dict] | tuple[dict, ...],
        *,
        current_frame: int,
        last_applied_generation: int,
        retention_frames: int,
        target_type: str,
    ) -> None:
        """Admit only workers first observed no later than the cohort deadline."""

        if retention_frames < 0:
            raise ValueError("retention_frames must be >= 0")

        accepted: list[dict] = []
        for response in responses:
            if not response or "error" in response:
                continue
            if str(response.get("planner_mode")) != "collect":
                continue
            if str(response.get("target_reward_type")) != str(target_type):
                continue
            key = self._identity(response)
            if key is None:
                continue
            generation, root_frame, _worker = key
            if key not in self._first_seen_frame:
                self._first_seen_frame[key] = int(current_frame)
                self._target_type[key] = str(target_type)
            first_seen = int(self._first_seen_frame[key])
            arrival_age = first_seen - int(root_frame)

            try:
                self._worker_compute_ms[key] = float(response.get("compute_ms", 0.0))
            except (TypeError, ValueError):
                pass
            try:
                self._worker_exact_steps[key] = int(response.get("collect_exact_frame_steps", 0))
            except (TypeError, ValueError):
                pass

            if arrival_age > int(self.deadline_frames):
                self._late_arrival_age[key] = int(arrival_age)
                continue
            accepted.append(dict(response))

        super().ingest(
            accepted,
            current_frame=current_frame,
            last_applied_generation=last_applied_generation,
            retention_frames=retention_frames,
            target_type=target_type,
        )

        # ``CollectResponseCache`` can only purge responses it admitted.  Late
        # observations live solely in our timing tables, so retire those using
        # the same generation/age/target lifecycle explicitly.
        for key in list(self._first_seen_frame):
            generation, root_frame, _worker = key
            age = int(current_frame) - int(root_frame)
            if (
                generation <= int(last_applied_generation)
                or age < 0
                or age > int(retention_frames)
                or self._target_type.get(key) != str(target_type)
            ):
                self._first_seen_frame.pop(key, None)
                self._target_type.pop(key, None)
                self._late_arrival_age.pop(key, None)
                self._worker_compute_ms.pop(key, None)
                self._worker_exact_steps.pop(key, None)

    def timing_snapshot(
        self,
        *,
        generation: int,
        root_frame: int,
        current_frame: int,
        expected_workers: int,
    ) -> dict:
        """Return auditable deadline/latency telemetry for one cohort."""

        if int(expected_workers) <= 0:
            raise ValueError("expected_workers must be > 0")
        generation = int(generation)
        root_frame = int(root_frame)
        current_frame = int(current_frame)
        expected = set(range(int(expected_workers)))

        arrivals: dict[int, int] = {}
        compute_ms: dict[int, float] = {}
        exact_steps: dict[int, int] = {}
        late: set[int] = set()
        for key, seen_frame in self._first_seen_frame.items():
            key_generation, key_root, worker = key
            if key_generation != generation or key_root != root_frame:
                continue
            age = int(seen_frame) - root_frame
            arrivals[int(worker)] = int(age)
            if key in self._worker_compute_ms:
                compute_ms[int(worker)] = float(self._worker_compute_ms[key])
            if key in self._worker_exact_steps:
                exact_steps[int(worker)] = int(self._worker_exact_steps[key])
            if age > int(self.deadline_frames):
                late.add(int(worker))

        on_time = {worker for worker, age in arrivals.items() if age <= self.deadline_frames}
        unseen = expected - set(arrivals)
        missing_on_time = expected - on_time
        quorum_age = None
        if expected.issubset(on_time):
            quorum_age = max(arrivals[worker] for worker in expected)

        current_age = current_frame - root_frame
        if quorum_age is not None:
            closure_reason = "quorum"
        elif current_age >= int(self.deadline_frames):
            closure_reason = "deadline"
        else:
            closure_reason = "waiting"

        return {
            "generation": generation,
            "root_frame": root_frame,
            "current_age_frames": int(current_age),
            "deadline_frames": int(self.deadline_frames),
            "closure_reason": closure_reason,
            "worker_arrival_age_frames": {str(k): arrivals[k] for k in sorted(arrivals)},
            "worker_deadline_slack_frames": {
                str(k): int(self.deadline_frames) - arrivals[k] for k in sorted(arrivals)
            },
            "worker_compute_ms": {str(k): compute_ms[k] for k in sorted(compute_ms)},
            "worker_exact_frame_steps": {str(k): exact_steps[k] for k in sorted(exact_steps)},
            "on_time_workers": sorted(on_time),
            "late_workers": sorted(late),
            "unseen_workers": sorted(unseen),
            "missing_on_time_workers": sorted(missing_on_time),
            "quorum_arrival_age_frames": quorum_age,
            "quorum_deadline_margin_frames": (
                None if quorum_age is None else int(self.deadline_frames) - int(quorum_age)
            ),
        }
