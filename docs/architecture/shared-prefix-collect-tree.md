# Shared-Prefix COLLECT Tree

Issue: #32 forward-model trajectory planner

## Context

V28/V29 make asynchronous COLLECT semantically exact:

- delayed reward branches preserve their historical root and phase;
- live action lineage must match the branch prefix;
- remaining exact-Mesen proof must cover the next authority commitment;
- a worker cohort must be complete before reward ranking.

The remaining problem is compute duplication. Every delayed reward branch starts
with the same deterministic authority continuation before its scheduled handoff.
Replaying that prefix independently does not add evidence.

## Tree contract

For handoffs at 8f and 12f:

```text
root
  |
  +-- authority continuation, simulated once
       |
       +-- exact checkpoint @ +8f
       |    +-- reward chunk A -> suffix proof
       |    +-- reward chunk B -> suffix proof
       |    +-- ...
       |
       +-- exact checkpoint @ +12f
            +-- reward chunk A -> suffix proof
            +-- reward chunk B -> suffix proof
            +-- ...
            +-- continuation-only anchor -> suffix proof
```

The branch-point savestates are exact Mesen states. Worker processes may restore
them because the same authority root savestate is already shared across workers.
The manifest is published only after every branch-point state has materialized.

Cross-worker state sharing is an optimization, not a correctness dependency. If
the manifest is missing, late, malformed, or terminal during trunk evaluation,
the worker falls back to V28's independent root replay.

## Proof identity is unchanged

A shared suffix still returns a complete root-relative schedule:

```text
authority continuation[0:handoff]
+ reward maneuver
+ declared continuation tail
```

`trajectory_frames` is also root-relative. Therefore V29 authority does not need
a special validator. It continues to apply:

```text
actual_authority_buttons[root:current]
    == candidate_buttons[0:source_age]

trajectory_frames - source_age >= commit_frames
```

Shared computation changes how evidence is produced, not what the evidence means.

## Proof horizon

The minimum horizon for a configured source-age target `L` and next commitment
`Q` is:

```text
proof_horizon >= L + Q
```

For the current default target:

```text
L = 16f
Q = 4f
proof_horizon = 20f
```

This is not a freshness claim. It is the compute horizon needed to guarantee one
more exact commitment for results arriving within the configured latency target.
A younger proof can have more lease; an older proof expires naturally.

## Budget

For 8 reward chunks, two handoffs `(8, 12)`, one continuation anchor, and a 20f
proof horizon:

```text
naive root replay:
  (8 chunks * 2 handoffs + 1 anchor) * 20f
  = 340 exact frame steps

shared tree:
  trunk to latest handoff                 12f
  8 branches from +8f:  8 * (20 - 8)     96f
  8 branches from +12f: 8 * (20 - 12)    64f
  continuation anchor:      (20 - 12)      8f
                                            ---
                                            180f
```

The same topology with the older 24f horizon costs 248f instead of 408f.

## Worker-count rule

Extra workers must not duplicate reward chunks. Worker count is compute capacity,
not search-vocabulary size. Workers with no unique chunk return coverage-only
responses so V29 cohort quorum can still complete without redundant Mesen work.

## Validation gate

V31 is still source/sandbox gated. Before World 1-1 live validation, verify:

1. branch-point savestates produced by worker 0 restore identically in peer Mesen workers;
2. manifest fallback returns to independent V28 exact replay without recursion;
3. aggregate exact-step telemetry matches the expected shared-tree budget;
4. delayed reward proofs still preserve root/phase and pass the unchanged V29 lineage/lease selector;
5. deterministic Star interception remains behaviorally equivalent or better than V29.
