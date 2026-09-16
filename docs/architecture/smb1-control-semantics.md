# SMB1 Control Semantics

This document records durable controller semantics used by Fami Pixel. Documentary control rules are useful for organizing the action space; Mesen execution remains the final authority for what actually happens.

## Frame-explicit control

SMB1 movement is not naturally represented by a few fixed jump macros. The useful control dimensions are:

```text
horizontal : LEFT / NEUTRAL / RIGHT
run        : B released / B held
jump       : A released / A held
duration   : explicit emulator frames
```

The planner therefore composes short controller-state schedules.

## A hold controls jump duration

Jump height and trajectory depend on how long A remains held. Releasing A and pressing it again creates a new jump edge only when the game is in a state that can consume that edge.

## B changes reachable trajectories

`RIGHT+B` and `RIGHT+A+B` are not cosmetic variants. Running speed changes reachable future states and can be required for longer crossings.

## Airborne steering remains meaningful

Horizontal input can continue to affect Mario while airborne. Jump initiation is therefore not the entire trajectory.

## Re-arm semantics

Machine evidence exposed a critical control fact: repeating held `RIGHT+A+B` does not manufacture a fresh A press after landing.

A reusable forward jump schedule is therefore commonly represented as:

```text
RIGHT+B     1f   # release A / re-arm edge
RIGHT+A+B  15f   # sustained jump hold
RIGHT+B     tail # continue forward without A
```

The exact duration is scenario-dependent and should be validated in Mesen rather than treated as universal physics.

## Commitment continuity

A safety-critical multi-frame crossing may span several live control quanta. Replanning must preserve schedule age from the original commitment root.

Incorrect behavior:

```text
grounded jump begins
→ next control quantum sees airborne hazard
→ planner restarts RIGHT+B 1f
→ A is released after leaving support
→ trajectory collapses
```

Correct behavior:

```text
root frame fixed at commitment start
age = 0, 4, 8, 12, ...
→ same schedule continues
→ commitment clears on authoritative landing / invalidation
```

This is an execution invariant, not merely a jump heuristic.

## Manual versus machine truth

```text
Official control documentation
    = intended player-control semantics

Mesen + SMB1 state
    = executable machine truth

Planner
    = search over admissible controller schedules
```

Manual statements should constrain action design, not be promoted directly into collision, reward, or terminal truth.
