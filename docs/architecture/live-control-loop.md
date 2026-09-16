# Live Control Loop

Fami Pixel uses a continuous authoritative Mesen trajectory plus asynchronous counterfactual planning.

## Core invariant

> **The authoritative emulator never waits for planning.**

```text
authoritative Mesen
  step / observe / publish continuously
          |
          +---- newest checkpoint ----> shadow worker pool
          |                               restore exact state
          |                               evaluate candidates
          |                               publish result
          |
          +<--- newest admissible plan ---+
```

## Isolation

Counterfactual save/load operations run only in shadow Mesen processes. The live authority process never restores a planner state.

## Latest-value semantics

Planning is not FIFO. A newer root supersedes stale work. Intermediate snapshots may be dropped. Late results may be rejected by frame age.

This is deliberate: an old answer about a world that no longer exists is not improved by being delivered reliably.

## Control quantum and schedule age

A selected plan is a schedule, not a single button. The authority indexes the schedule by native-frame age from the plan or commitment root.

For ordinary plans, a fresh current result may replace an older one. For safety-critical commitments, the original root remains fixed until authoritative completion/invalidation so control quanta do not restart the schedule.

## Current-scene preemption

Live semantic evidence may outrank asynchronous progress planning. The current priority contract is:

```text
1. active/current gap survival commitment
2. current enemy landing-zone safety
3. active reward collection objective
4. asynchronous progress / forward-model result
```

This prevents stale progress plans from overwriting a current hazard response.

## Staleness is semantic, not only numeric

A result may be numerically recent yet semantically unsafe if it assumes an earlier airborne/landing phase. Live planners must consider current geometry and commitment state, not only `plan_age`.

## Failure containment

Planner workers may miss deadlines, crash, or be terminated without becoming machine authority. The supervisor owns process cleanup so terminal live evidence returns control to the caller even when native teardown is imperfect.

## Telemetry

Useful live evidence includes:

```text
authoritative frame
applied action
plan / commitment root frame
schedule age
planner source
compute time
semantic hazard/reward state
objective
terminal outcome
```

Timing defects must be visible rather than hidden behind playback or blocking waits.
