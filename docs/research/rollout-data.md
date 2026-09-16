# Rollout Data and Sampling

Fami Pixel learns from authoritative Mesen rollouts. Dataset-generation policy decides **which roots to explore**; it does not redefine transition truth.

## Sample contract

A rollout sample records one root state, one candidate schedule, and the resulting exact Mesen outcome. Useful fields include:

```text
source / generation / worker
candidate name / schedule / horizon
start machine and SMB1 state
end machine and SMB1 state
progress / max-X
terminal / death / completion
no-progress / planning-risk labels
compute time
```

Generated datasets live under `build/datasets/` and are not committed as source.

## Root identity and grouping

Candidate rows from the same checkpoint share one root identity. Training/evaluation splits must group by root so neighboring counterfactual outcomes do not leak across partitions.

## Greedy collection

A simple collector may evaluate all candidates from one root and promote the best surviving outcome as the next root. This is useful for transition coverage but under-samples difficult hazard boundaries.

## Hazard-focused collection

Hazard-focused sampling may promote several non-terminal children from each root, for example:

- low-progress survivors;
- near-zero-progress boundary cases;
- high-progress survivors that keep the frontier moving.

Children are deduplicated by structured SMB1 state. Terminal outcomes remain labeled samples but are not promoted as future roots.

## Label discipline

Authoritative labels come from Mesen execution. Derived planning labels such as `doomed_within_probe`, `no_progress`, or hazard proximity must remain distinguishable from direct terminal events.

## Relationship to live control

Offline sampling can improve learned guidance, but it never changes live authority. Live candidate selection remains subject to current-scene safety and exact Mesen validation where required.
