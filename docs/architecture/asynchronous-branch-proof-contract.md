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

For normal PROGRESS trajectories the semantic safety bit is
`trajectory_safe_resolved`. For bounded COLLECT proofs the corresponding bit is
`reward_prefix_safe`: an exact finite reward prefix may be alive without ending
in a landing/capability terminal event. The lineage and proof-lease equations are
otherwise identical.

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
    -> required evaluation coverage / anchor quorum where applicable
    -> action-lineage filter
    -> remaining-proof filter
    -> exact-Mesen semantic safety filter
    -> objective-specific exact result ordering
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

`--plan-freshness` is a resource/cache latency parameter for V27+ PROGRESS, not a
semantic proof criterion. A result older than that window can still be exact if
its lineage matches and sufficient proof lease remains. Conversely, a very
fresh result is invalid immediately after one divergent authority action.

## Delay-compensated COLLECT handoff

V25's original reward contract proved only a four-frame chunk. With asynchronous
worker latency, a result arriving four or more frames later had no future proof
lease left, even before considering action-lineage divergence.

V28 therefore uses an MPC-style shifted warm start. Each checkpoint request may
carry the currently selected authority schedule projected from the checkpoint
root. Reward workers evaluate bounded candidates of the form:

```text
current-plan continuation for D frames
    -> reward chunk
    -> A-released continuation tail
```

where the source-level V28 gate currently uses `D in {8, 12}` and a 24-frame
exact proof horizon.

This has two useful timing regimes:

```text
result arrives before handoff D
    -> lineage matches the continuation prefix
    -> authority can adopt the old proof at its real age
    -> continue to D, then execute the reward maneuver

result arrives after handoff D
    -> valid only if live authority actually executed the same reward branch
    -> otherwise lineage mismatch rejects it
```

A `collect_continue_authority` candidate evaluates the continuation-only warm
start. It is not a positive reward heuristic; it exists so the selected frontier
contains one exact branch representing "keep doing what authority is already
doing" while other reward futures compute.

The continuation request is advisory input to worker proposal generation. Final
validity still comes from the actual authority action ledger. If an emergency or
higher-priority guard changes the real buttons after the checkpoint, the warm
start proof simply fails lineage validation.

## Required regression cases

1. exact lineage match -> continue at age `N`;
2. one-frame lineage mismatch -> reject;
3. newer partial + older complete + match -> older proof may continue;
4. newer partial + older complete + mismatch -> reject older proof;
5. higher-score mismatch + lower-score match -> reachable lower-score wins;
6. `trajectory_frames < source_age + commit_frames` -> reject expired proof;
7. worker evaluates multiple branches -> all branch proofs remain selectable;
8. delayed COLLECT result before handoff -> branch remains reachable;
9. authority passes an unexecuted handoff -> that reward branch is rejected;
10. diverged reward handoffs + matching continuation anchor -> anchor remains eligible;
11. legacy 4f COLLECT proof at age 4 with a 4f next commit -> proof lease expired.

## Validation status

V27 implements branch-level delayed PROGRESS lineage filtering and source-root
phase preservation. V28 adds the delay-compensated COLLECT proposal/selection
contract described above.

This is still **source-level / deterministic-test-gate work**. It is not a new
World 1-1 field-validation claim. Before promoting V28 to live acceptance, the
unit/smoke suite and deterministic Mesen reward scenario should confirm:

- continuation request generation uses the actual selected plan phase;
- 8f/12f delayed reward branches survive or reject exactly according to lineage;
- the 24f proof lease covers the next 4f commitment;
- SURVIVE > landing safety > COLLECT > PROGRESS ordering remains unchanged;
- the existing V26 World 1-1 and V25 Star evidence are not reinterpreted as V28 evidence.
