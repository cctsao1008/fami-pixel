# Forward-Model Trajectory Planning

Fami Pixel plans **outcomes**, not only local hazard scores.

> Semantic perception proposes what matters. Learned models rank cheaply. Mesen proves what actually happens.

## Architecture

```text
Authoritative Mesen state
        |
        v
Semantic world model
  - Mario state/capabilities
  - hostile enemies/clusters
  - terrain/gap evidence
  - reward objects/targets
        |
        v
Objective manager
  SURVIVE / COLLECT / PROGRESS / RECOVER
        |
        v
Bounded action-chunk generator
        |
        v
Cheap learned ordering / pruning
        |
        v
Parallel Mesen save-state branches
        |
        v
Event-horizon trajectory evaluation
        |
        v
Lexicographic outcome selection
        |
        v
Execute bounded prefix / commitment
        |
        +---- observe authoritative state and replan
```

## Mesen is the forward model

A branch is evaluated by restoring an exact authoritative checkpoint in an isolated shadow process, applying a bounded controller schedule, stepping real emulation frames, reading structured SMB1 state, and stopping on a meaningful event.

No hand-written ballistic approximation may override the branch result.

## Event horizon

Useful terminal/evaluation events include:

```text
DEATH
WIN / LEVEL_COMPLETE
REWARD_COLLECTED
CAPABILITY_CHANGED
LANDED
HORIZON_UNRESOLVED
```

`HORIZON_UNRESOLVED` is not equivalent to safe.

## Lexicographic selection

A useful outcome ordering is:

```text
WIN
SAFE_REWARD_COLLECTED
SAFE_CAPABILITY_GAIN
SAFE_LANDING_WITH_PROGRESS
SAFE_PROGRESS
HORIZON_UNRESOLVED
DEATH
```

Tie-breakers may use progress, target approach, landing quality, learned risk, or compute cost. A learned estimate must never override exact Mesen evidence that another branch is safer or superior.

## Receding horizon and commitment

Ordinary actions execute only a short prefix before replanning. This does **not** mean every control quantum may restart a safety-critical multi-frame action.

A crossing or other committed maneuver keeps one root and advances schedule age across quanta until authoritative landing or explicit invalidation. Lower-priority stale results cannot break the commitment.

## Action vocabulary

Branching remains bounded but composable. Useful chunks include run, coast/release, short LEFT/RIGHT corrections, and A hold/release segments. Fami Pixel intentionally does not expand every NES button combination blindly.

## Terrain and landing

Enemy-only landing telemetry is not terrain safety. Terrain evidence should distinguish positive support/gap knowledge from unknown state; `UNKNOWN` must not silently become safe.

Exact branch outcome remains the final safety oracle when available.

## Regression discipline

Development uses deterministic local save-state roots for narrow failures before replaying a full level. Example classes include first-enemy, enemy-cluster landing, pit crossing, reward interception, and stall recovery.

A full World 1-1 run is integration evidence. It does not replace local scenario gates.

## Current milestone

The V26 integration run on 2026-09-16 completed World 1-1 autonomously after deterministic enemy and pit regressions were closed locally. The broader research track remains open for richer search, terrain-validity semantics, and learned rank/prune evaluation.
