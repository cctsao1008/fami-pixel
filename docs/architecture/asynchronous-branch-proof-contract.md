# Asynchronous Branch-Proof Contract

Issue: #32 forward-model trajectory planner

## Problem

A Mesen rollout is exact only for the state and input lineage from which it was
simulated. In the live controller a worker result can arrive several authority
frames after its source checkpoint. Source age by itself does not say whether
the proof still describes the live state.

The unsafe pattern is:

```text
historical proof rooted at R
        -> arrives at current frame C
        -> overwrite root with C
        -> replay candidate from age 0
```

That executes a different trajectory on a different state.

## Valid delayed proof

For source root `R`, current authority frame `C`, source age `N=C-R`, and next
commitment `Q` frames, a branch proof is eligible only when:

```text
actual_authority_buttons[R:C]
    == candidate_buttons[0:N]

and

trajectory_frames - N >= Q
```

The first clause is **action-lineage reachability**. The second is the
**remaining proof lease**.

Only after both clauses pass may candidate scores be compared.

## Authority action ledger

The ledger records the final NES buttons actually applied for each transition:

```text
frame f -> buttons used for transition f -> f+1
```

It must be recorded at the final authority output point, after SURVIVE, landing
safety, COLLECT, PROGRESS, emergency, and watchdog policy composition.

Planner intent, candidate names, or pre-override schedules are not substitutes
for this ledger.

## Branch proof identity

A worker is compute capacity, not proof identity. When one worker evaluates
multiple candidates, every exact branch result must be returned to authority.
The cache identity is therefore conceptually:

```text
(generation, root_frame, candidate)
```

rather than `(generation, root_frame, worker)`.

This prevents a shard-local high-score result from hiding a lower-score branch
that is the only candidate still reachable by current action lineage.

## Selection order

```text
same-root branch proofs
    -> required evaluation coverage / anchor quorum
    -> action-lineage filter
    -> remaining-proof filter
    -> exact-Mesen safe/resolved filter
    -> lexicographic exact outcome score
```

Reachability precedes score.

## Phase preservation

When a delayed branch remains valid, preserve its original root:

```text
applied_plan_root = source_root
schedule_age = current_frame - source_root
```

Do not restamp the plan to current time and reset schedule age to zero.

## Freshness

`--plan-freshness` is a resource/cache latency parameter for V27 PROGRESS, not a
semantic proof criterion. A result older than that window can still be exact if
its lineage matches and sufficient proof lease remains. Conversely, a very
fresh result is invalid immediately after one divergent authority action.

## Required regression cases

1. exact lineage match -> continue at age `N`;
2. one-frame lineage mismatch -> reject;
3. newer partial + older complete + match -> older proof may continue;
4. newer partial + older complete + mismatch -> reject older proof;
5. higher-score mismatch + lower-score match -> reachable lower-score wins;
6. `trajectory_frames < source_age + commit_frames` -> reject expired proof;
7. worker evaluates multiple branches -> all branch proofs remain selectable.

## Follow-on work

This contract does not solve long worker latency by itself. If most newly
computed branches become unreachable before arrival, the next architectural
step is a delay-compensated/persistent frontier with a current-plan continuation
anchor (MPC-style shifted warm start). That should be evaluated only after this
proof contract is stable.
