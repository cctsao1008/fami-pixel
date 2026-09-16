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

## Bounded multi-chunk search

The ordinary PROGRESS search is no longer limited to a fixed list of single macros. A bounded chunk vocabulary can compose short sequences from primitives such as:

```text
run
coast / release
brake / backtrack
A-hold continuation
jump re-arm + short hold
jump re-arm + long hold
```

Search depth counts composable chunks rather than raw NES frames. The current experimental implementation expands depth 3 and rejects prefixes longer than 28 frames before learned ranking.

The generator remains deliberately finite and inspectable. It does not expand unrestricted NES button combinations.

## Learned rank/prune contract

The tiny SMB1 surrogate is used before exact rollout to reduce emulator work:

```text
all bounded candidates
        |
        v
TinySurrogateMLP
  predicted delta-X
  risk probability
  no-progress probability
        |
        v
fixed top-K frontier
        |
        v
exact Mesen evaluation
```

The learned model is **not** a branch authority. Progress prediction is the primary ordering signal; risk and no-progress are soft ordering signals rather than hard vetoes because their calibration is weaker. Known-good baseline maneuvers remain mandatory diversity anchors so an OOD learned score cannot prune every established escape/action family.

The historical model feature contract observes only the first two action commands. Multi-chunk plans may be longer than that window. In those cases the surrogate is explicitly ranking a visible prefix while Mesen evaluates the complete candidate. This limitation is telemetry, not hidden certainty.

Current experimental budget:

```text
search depth = 3 chunks
top-K        = 12 candidate trajectories
live prefix  = 4 authoritative frames
```

Each worker records generated/pruned/evaluated counts and the learned prediction attached to the selected exact-Mesen branch.

## Receding horizon and commitment

Ordinary actions execute only a short prefix before replanning. This does **not** mean every control quantum may restart a safety-critical multi-frame action.

A crossing or other committed maneuver keeps one root and advances schedule age across quanta until authoritative landing or explicit invalidation. Lower-priority stale results cannot break the commitment.

Current policy authority ordering remains:

```text
current SURVIVE commitment
        >
current landing-zone enemy preemption
        >
sticky COLLECT objective
        >
learned-ranked PROGRESS search
```

The learned-ranked search therefore cannot displace an already-active V26 safety commitment.

## Terrain and landing

Enemy-only landing telemetry is not terrain safety. Terrain evidence distinguishes:

```text
SAFE
GAP
UNKNOWN
```

`UNKNOWN` never silently becomes safe. Exact branch outcome remains the final trajectory oracle when available.

## Regression discipline

Development uses deterministic local save-state roots for narrow failures before replaying a full level. Example classes include first-enemy, enemy-cluster landing, pit crossing, reward interception, and stall recovery.

The deterministic `tools/smb1_bounded_search_probe.py` gate exercises the complete proposal pipeline from one scenario root:

```text
bounded generation
-> TinySurrogateMLP rank/prune
-> top-K exact Mesen rollouts
-> safe resolved selection
```

A full World 1-1 run is integration evidence. It does not replace local scenario gates.

## Current milestone

The V26 integration run on 2026-09-16 completed World 1-1 autonomously after deterministic enemy and pit regressions were closed locally. Terrain `SAFE/GAP/UNKNOWN` acceptance is now complete under issue #31.

V27 is the source-level experimental implementation of the remaining #32 rank/prune architecture: bounded depth-3 multi-chunk PROGRESS generation, TinySurrogateMLP top-K pruning, exact Mesen evaluation, and 4-frame receding-horizon execution while retaining V26 SURVIVE and V25 COLLECT authority. It is **not field-validated yet**; deterministic bounded-search probes are the next gate before another full live run.
