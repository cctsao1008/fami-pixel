# SMB1 Learned Surrogate

The learned SMB1 model is a **cheap proposal/ranking model**, not machine authority and not an end-to-end controller.

```text
Mesen rollout teacher
      |
      v
(state, candidate) dataset
      |
      v
small learned surrogate
      |
      v
cheap candidate ranking / pruning
      |
      v
selected Mesen shadow branches
      |
      v
authoritative choice
```

## Current baseline

The current tiny model is intentionally small and CPU-friendly:

```text
34 inputs
  -> 16 hidden
  -> 3 outputs
```

Current outputs represent:

```text
predicted delta-X
risk probability
no-progress probability
```

The artifact format is versioned and stored as a generated model under `build/` during local experiments.

## What the baseline is good at

Current evidence shows useful forward-progress and no-progress prediction. Risk recall is useful as a warning signal, but probability calibration/precision is not strong enough to grant veto authority.

Therefore the durable usage remains:

```text
many candidates
  -> learned rank/prune
  -> top-K exact Mesen rollouts
  -> Mesen-authoritative selection
```

## Grouped validation

Dataset splitting and metrics must respect root-state grouping so near-identical candidate samples from one checkpoint do not leak across train/validation/test.

Useful evaluation includes:

- delta-X error;
- death/doomed-risk recall and precision;
- no-progress recall;
- top-K oracle coverage;
- inference latency;
- robustness on unseen root groups.

## Authority boundary

A prediction never creates `DIED`, `LEVEL_COMPLETED`, `LANDED`, or `REWARD_COLLECTED` machine events.

When learned guidance disagrees with exact Mesen evidence, Mesen wins.
