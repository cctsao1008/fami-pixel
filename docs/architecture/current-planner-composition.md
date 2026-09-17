# Current SMB1 planner composition

Status: characterization baseline for issue #35.

This document records the **effective current planner composition before structural extraction**. It is intentionally descriptive: it does not redefine behavior and it does not make historical example modules authoritative architecture. The purpose is to freeze what the repository currently does so code can move into stable `src/fami_pixel/` layers without silently changing policy.

## Authority model

The current system keeps these boundaries:

```text
live Mesen core              = machine / transition authority
shadow Mesen branches        = counterfactual evidence
SMB1 semantic/domain layer   = interpretation
planner/control composition  = decision and arbitration
learned surrogate            = candidate rank/prune helper only
telemetry/evidence           = observation, not control authority
```

Exact Mesen branch outcomes remain authoritative over heuristic radar or surrogate predictions. Historical asynchronous results are admissible only when their authority action lineage still matches and enough exact proof horizon remains for the next commitment.

## Current runnable composition root

The latest runnable entry point is:

```text
examples/mesen_smb_checkpoint_planner_v35.py
```

`v35.main()` installs the current override stack and then dispatches through `v23.main()`. This means the active composition is still assembled by mutating functions/globals owned by historical planner modules. That is the architectural debt tracked by #35.

The installer dependency chain is:

```text
V35
  -> V34
      -> V33
          -> V32
              -> V31
                  -> V30
                      -> V29
                          -> V28
                              -> V27
                                  -> installs V24
                                  -> installs V25
                                  -> installs V26
```

This is not a simple inheritance chain. Later installers replace selected delegates in earlier modules. In particular, several generations replace `v26._BASE_V25_PLAN`, while worker-side COLLECT evaluation is replaced through V28/V30/V31 and PROGRESS evaluation remains rooted in V27.

## Effective decision ordering

The effective behavioral priority remains:

```text
current SURVIVE commitment
    > current landing-zone enemy preemption
        > sticky / explicit COLLECT
            > PROGRESS
```

V26 owns the current-scene SURVIVE / gap / landing-preemption layer. Later versions replace only the lower COLLECT/PROGRESS delegate rather than bypassing V26.

### SURVIVE / landing preemption

Effective owner: V26 policy layer.

Responsibilities include:

- current positive-gap survival commitment;
- current landing-zone enemy preemption;
- support/grounding-aware continuation;
- preserving safety priority over reward/progress behavior.

The current composition must preserve this ordering during extraction.

### COLLECT: Star

Effective owner: V35 current-root synchronous Star micro-MPC.

When the current semantic target is a Star and the authority core is available, V35:

1. saves the exact current live root;
2. evaluates the bounded 8-candidate, 4-frame reward chunk vocabulary on the authority-local Mesen core;
3. restores the exact root after each speculative branch and after the search;
4. selects the best safe 4-frame Star-directed prefix;
5. returns a proof rooted at the real current frame with source age zero;
6. replans after the live 4-frame commit.

Speculative save/restore/controller writes are executed with authority-action-ledger recording suspended, so counterfactual actions cannot become live lineage history.

The synchronous Star path is a deliberate exception to the asynchronous worker path: live game frames do not advance while that small current-root search runs.

### COLLECT: asynchronous non-Star/fallback path

Effective owner: V34 over the V33/V32/V31/V30/V29/V28 stack.

The accumulated responsibilities are:

- delayed reward handoff branches;
- eager +4f handoff in addition to later handoffs;
- cohort-coherent selection;
- shared-prefix reward-tree evaluation;
- recursion-safe exact fallback;
- deadline / handoff admission;
- irreversible expiry of missed handoffs;
- historical-root lineage and proof-lease validation before adoption.

V35 delegates non-Star/fallback COLLECT to this V34 path.

### PROGRESS

Effective owner: V27 bounded multi-chunk search.

Responsibilities include:

- bounded depth multi-chunk candidate generation;
- learned surrogate rank/prune only;
- exact Mesen evaluation of the retained frontier;
- required anchor candidates;
- branch-level proof payloads;
- authority action lineage validation;
- proof-lease validation;
- preservation of historical proof root/phase when a stale result is still exactly reachable.

The learned surrogate is advisory. It cannot override an exact Mesen-proven branch outcome.

## Runtime / lifecycle ownership

The current planner already depends on stable runtime helpers under `src/fami_pixel/runtime/` for process and checkpoint infrastructure. The V23-era live stack preserves:

- per-run isolated IPC/checkpoint directories;
- parent-lease worker exit;
- Windows kill-on-close Job Object containment when available;
- explicit process cleanup/reaping;
- checkpoint archive helpers.

These runtime concerns should stay separate from planner policy during #35 extraction.

## Stable domain primitives already outside examples

Important current behavior is already implemented in package modules rather than historical examples, including:

```text
src/fami_pixel/adapters/mesen/          native emulator boundary
src/fami_pixel/games/smb1/              actions, state, events, radar, trajectory/search primitives
src/fami_pixel/games/smb1/action_lineage.py
src/fami_pixel/learning/                surrogate model and rollout learning
src/fami_pixel/runtime/                 process/checkpoint lifecycle
src/fami_pixel/telemetry/               evidence/UI support
```

The remaining debt is primarily **composition/orchestration ownership**, not the absence of reusable primitives.

## Mutation map that must disappear from the active architecture

The current composition is created through runtime replacement of symbols in historical modules. Characterization must preserve the effects of these mutations before removing them. High-value mutation points include:

```text
v26._BASE_V25_PLAN
v23.authority_main
v23.PLANNER_NAME
v23.shadow_worker_main / forward-search delegates
v28 worker-side COLLECT search entry
v25 PROGRESS baseline payload
v24/V27 partial-progress selector
base.set_nes_controller_state instrumentation/capture wrappers
```

The target architecture should make these dependencies explicit constructor/composition inputs instead of ambient module mutation.

## Extraction boundaries

The intended stable ownership is:

```text
src/fami_pixel/planning/
    pure objective/search/proof/cohort/admission policies

src/fami_pixel/control/
    live authority orchestration, preemption, bounded commits,
    worker-response intake, current-root/generation management,
    composition root

src/fami_pixel/runtime/
    processes, IPC, checkpoint archive, OS containment
```

Game-specific state decoding and Mesen trajectory mechanics remain under the existing adapter/domain layers unless a later refactor has a concrete reason to move them.

## Incremental migration order

Do not rewrite the stack in one step. Use this order:

1. **Characterize V35 composition** — this document plus source-level tests that freeze the installer/delegation graph.
2. Introduce stable `planning/` and `control/` package boundaries with behavior-neutral contracts.
3. Extract pure admission/lineage/cohort/objective helpers first; keep historical wrappers calling the stable functions.
4. Extract the lower COLLECT/PROGRESS arbitration currently assigned through `v26._BASE_V25_PLAN`.
5. Extract authority-loop orchestration and worker-response intake into `control/`.
6. Create one explicit stable SMB1 composition root.
7. Point a thin current runner at that stable root.
8. Retain V1-V35 examples as research provenance/regression references rather than active architecture owners.

At every slice, existing deterministic gates remain authoritative: Star 4f replay, strict multi-Goomba landing, pit/terrain validity, action-lineage/proof-lease tests, and process-lifecycle tests.

## Definition of equivalence for #35

Structural extraction is equivalent only if all of the following remain true:

- SURVIVE/landing preemption remains above COLLECT and PROGRESS;
- Star current-root synchronous exact-Mesen behavior remains current-root and 4-frame receding;
- non-Star COLLECT retains current handoff/cohort/deadline semantics;
- PROGRESS retains bounded multi-chunk search and exact-Mesen final authority;
- historical asynchronous proofs require lineage + proof lease before adoption;
- speculative branches never pollute authority action history;
- runtime worker isolation/cleanup remains unchanged;
- surrogate authority does not increase;
- deterministic behavioral acceptance from #32 remains green.

Until the active runner no longer depends on the versioned override chain, this document is a characterization baseline, not the final architecture.