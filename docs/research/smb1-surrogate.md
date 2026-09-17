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

The current feature contract is explicitly versioned as:

```text
smb1-tiny-surrogate-features-v1
```

Newly written inference files use model format:

```text
fami-pixel-tiny-surrogate-v2
```

The V2 file stores the feature-schema identifier, fixed input width, output
schema, architecture, weights, and biases. The loader also accepts the older
`fami-pixel-tiny-surrogate-v1` standalone files when their implicit 34-input
feature shape and output schema match the current V1 feature extractor.
Incompatible feature or output schemas fail loudly.

## Versioned generated artifacts

Normal training outputs remain generated local artifacts under `build/` and are
not source-controlled. Running `tools/train_smb1_surrogate_mlp.py` without an
explicit output path auto-selects the next directory:

```text
build/models/
└─ smb1-surrogate-vNNN/
   ├─ model.json
   ├─ metadata.json
   ├─ metrics.json
   └─ dataset_manifest.json
```

The files have separate responsibilities:

- `model.json` is the runtime inference artifact: feature/output schema,
  architecture, weights, and biases.
- `metadata.json` records UTC creation time, Git commit/dirty state when
  available, trainer version, hyperparameters, split policy, experiment tags,
  optional note, and the authority-boundary statement.
- `metrics.json` stores train/validation/test metrics, chronological/OOD stress
  metrics, training duration, and measured inference latency when available.
- `dataset_manifest.json` fingerprints every input JSONL file with SHA-256,
  byte size, record count, source/root-group counts, rollout/feature/target
  schemas, and the grouped split summaries. The rollout data itself is not
  copied into the model artifact.

The artifact loader cross-checks metadata, dataset manifest, and `model.json`
against the current feature/output contract before returning a model.

### Training example

```text
python tools/train_smb1_surrogate_mlp.py build/rollouts/*.jsonl \
  --epochs 800 \
  --hidden 16 \
  --learning-rate 0.01 \
  --seed 22 \
  --tag baseline
```

The JSON report printed by the trainer includes the generated artifact directory
and its `model.json` path. Existing live planners that accept
`--surrogate-model` should receive that `model.json` file, for example:

```text
--surrogate-model build/models/smb1-surrogate-v001/model.json
```

`--output-model <path>` remains available as an optional extra standalone V2
model copy for older local scripts. It is not the primary training artifact.

## Generated versus promoted models

A generated artifact is evidence from one experiment. It is not automatically a
reference baseline and should stay under ignored `build/models/` storage.

Promotion is deliberate. A model should only be copied into a source-controlled
`models/` location after its provenance and evaluation evidence are reviewed,
for example:

```text
models/
└─ smb1-surrogate-baseline/
   ├─ model.json
   ├─ metadata.json
   ├─ metrics.json
   └─ dataset_manifest.json
```

Promotion does not grant additional control authority. It only states that the
artifact is a reviewed reference model suitable for reproducible experiments.
Top-K oracle coverage, risk behavior, OOD results, and dataset provenance should
be considered before promotion.

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

The model-selection split is grouped and hazard-stratified. A separate grouped
chronological split is retained as an OOD/distribution-shift stress test. Both
policies and their partition summaries are persisted in each model artifact.

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

The artifact contract records this explicitly as:

```text
learned model ranks/prunes candidates
  -> exact Mesen rollout provides authoritative trajectory evidence
  -> live Mesen remains machine authority
```
