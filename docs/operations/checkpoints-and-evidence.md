# Checkpoints, Evidence, and Deterministic Replay

Fami Pixel treats save states and run evidence as first-class research artifacts.

## Live run evidence

Live controllers persist structured evidence under local `build/` directories. A run may include:

```text
summary.json
final-frame.png
final-radar.json
final-state.json
timeline.jsonl
```

These artifacts are local/generated and are not source authority by themselves; they are evidence of one execution.

## Checkpoint archive

The live planner keeps rolling checkpoints for planning and a stable archive for post-run regression extraction:

```text
build/checkpoints/.../run-.../
  live-XXXXXX.mss
  archive/
    live-000001.mss
    live-000002.mss
    ...
```

A historical run that pruned a checkpoint before archival cannot reconstruct that exact root later. Timeline evidence may still identify the failure, but a new run is required to capture a deterministic state.

## Scenario extraction

A deterministic scenario consists of an exact Mesen state plus a manifest describing the selected live generation/frame and relevant SMB1 root state.

Scenario extraction must not silently substitute a later state for a missing earlier root. Moving past the event under investigation invalidates the regression.

## Regression discipline

For a narrow gameplay failure:

```text
1. locate the earliest meaningful failure/decision boundary
2. extract the exact save-state root
3. evaluate candidate trajectories from that state
4. encode the discovered invariant in tests
5. rerun the local scenario
6. only then run a full-level integration test
```

A full World 1-1 run is not a unit test.

## Event interpretation

Exact Mesen branch outcomes may be classified as death, landed, reward/capability change, level complete, or horizon unresolved. `HORIZON_UNRESOLVED` is not proof of safety.

## Evidence versus authority

```text
Mesen live trajectory      = machine authority
shadow branch              = exact counterfactual evidence
semantic radar             = interpretation
learned prediction         = heuristic evidence
run bundle                 = replay/audit evidence
```

Keep these roles explicit when writing tests, issues, and documentation.
