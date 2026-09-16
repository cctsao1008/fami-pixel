# Authority-Observed COLLECT Deadline Admission

Issue: #32 forward-model trajectory planner

## Why a deadline needs an admission clock

Delay-compensated COLLECT branches have explicit handoff frames. With the current
vocabulary the latest planned handoff is 12 native frames after the source root.
If authority continues the old plan beyond that handoff, an unexecuted reward
branch normally becomes unreachable by action lineage.

A deadline therefore cannot mean only:

```text
if current_age >= 12:
    rank whatever responses are in the cache now
```

That rule lets a worker first observed at age 16 enter a cohort that was already
considered closed at age 12. Worker latency can then change policy retroactively.

## Admission rule

V33 timestamps a worker response when the authoritative controller first observes
it:

```text
arrival_age = authority_first_seen_frame - root_frame
```

Cohort admission is irreversible:

```text
arrival_age <= deadline  -> admitted to ranking
arrival_age >  deadline  -> telemetry only; never admitted for that root
```

The current deadline is the latest scheduled COLLECT handoff:

```text
deadline = max(COLLECT_HANDOFF_FRAMES) = 12f
```

A response observed exactly at age 12 is on time because authority can still
select the schedule before applying the transition beginning at that phase.

## Clock authority

The deadline clock is the live Mesen native-frame clock, not worker wall time.

`compute_ms` is still recorded because it is useful for performance diagnosis,
but it is not a semantic deadline criterion. Authority and shadow workers can
advance emulated frames at different wall-clock rates; only the authority frame
identifies whether a delayed result was available before its useful handoff.

## Cohort policy

The resulting COLLECT selection order is:

```text
worker response first observed
        |
        +-- arrival_age <= deadline ? -- no --> latency telemetry only
        |                         |
        |                        yes
        v
admitted same-root cohort
        |
        +-- before deadline: require full worker quorum
        |
        +-- at/after deadline: close with admitted workers
        v
action-lineage reachability
        v
remaining exact proof lease
        v
reward-prefix safety
        v
reward/interception ranking
```

Deadline admission is separate from action-lineage validity. An on-time branch can
still be rejected immediately if authority executed a different prefix. A late
branch remains excluded even if its schedule would coincidentally match later.
This keeps the real-time policy deterministic.

## Telemetry

V33 publishes enough information into the normal timeline to audit the timing
contract:

- per-worker authority arrival age,
- per-worker deadline slack,
- per-worker `compute_ms`,
- per-worker exact Mesen frame-step count when available,
- on-time / late / unseen worker sets,
- quorum arrival age and margin,
- cohort closure reason (`waiting`, `quorum`, or `deadline`),
- recent late-worker observations retained for one response-retention window.

Policy cache entries are discarded after a generation is consumed. Timing
evidence intentionally survives for the bounded retention window so a late worker
that arrives after authority has already committed can still be diagnosed without
being reconsidered for control.

## Fault-injection acceptance

The regression gate stages a three-worker cohort:

```text
worker 0 first seen at age 4
worker 1 first seen at age 8
worker 2 missing at deadline age 12
```

At age 12 the cohort closes with workers 0 and 1. Worker 2 is then injected at age
16 with a deliberately stronger reward score. The selected branch must remain the
best on-time branch, and worker 2 must appear only in late-worker telemetry.

This is the required behavior even when the test deliberately leaves
`last_applied_generation` unchanged; deadline closure must be irreversible on its
own rather than relying on later generation consumption to hide the race.
