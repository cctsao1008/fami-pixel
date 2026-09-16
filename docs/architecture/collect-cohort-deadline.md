# COLLECT Cohort Deadline

Issue: #32 forward-model trajectory planner

## Why full quorum is not enough

V29 removed the COLLECT first-finisher race by waiting for every configured worker
before ranking a generation. That is deterministic, but it can become a permanent
barrier if one worker is slower than the useful branch-handoff window.

Delayed COLLECT already has explicit handoffs at 8f and 12f. A branch that starts
its reward maneuver at 12f is normally adoptable only through age 12. If live
authority keeps executing the previous continuation after that point, the exact
action-lineage validator will reject the branch when it finally arrives.

Therefore waiting indefinitely for full quorum cannot recover the missing branch;
it only delays a decision until the proof is no longer reachable.

## Deadline rule

The current deterministic closure rule is:

```text
cohort_deadline = max(collect_handoff_frames) = 12f
```

For each generation/root:

```text
age < 12f
    full worker quorum required

age >= 12f
    explicitly close the cohort with responses already received
    -> lineage filter
    -> proof-lease filter
    -> reward ranking
```

This is not "first finisher wins." Before the deadline, an incomplete cohort
cannot be consumed. At the deadline, missing work is treated as a missed real-time
budget and recorded as such.

## Interaction with individual handoffs

An 8f reward branch can be selected only while its action history remains
reachable. If the full cohort is not ready by age 8 and authority keeps following
the continuation, those 8f branches naturally disappear through lineage mismatch.
The 12f branches remain candidates until the final cohort deadline.

This creates a staged interpretation without special-case selector code:

```text
0..7f   : 8f and 12f branches may both remain reachable
8..11f  : unexecuted 8f branches drop out; 12f branches remain
12f     : close cohort; rank remaining reachable 12f/continuation proofs
>12f    : only branches whose action lineage was actually executed survive
```

## Older cohorts

A newer incomplete cohort does not erase an older cohort retained in the response
cache. Authority scans newest to oldest. An older complete or deadline-closed
cohort may still win only when its exact action lineage and proof lease reach the
current authority state.

## Missing workers

Deadline-closed selections publish:

- expected worker count;
- received worker count;
- missing worker IDs;
- whether full quorum was reached;
- cohort deadline and source age;
- lineage rejection counts.

Late results from a consumed generation are intentionally ignored. That is an
explicit deadline miss, not an accidental latency policy.

## Validation gate

Before live World 1-1 use, measure real worker latency and verify:

1. full quorum normally arrives before 12f under the shared-prefix V31 worker;
2. deadline closure prevents starvation under injected slow-worker conditions;
3. missed 8f branches are rejected by lineage after authority passes their handoff;
4. a 12f branch remains adoptable exactly at age 12 when continuation lineage matches;
5. deadline closure never bypasses SURVIVE/landing authority layers.
