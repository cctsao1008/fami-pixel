# System Architecture

Fami Pixel is a deterministic machine-control research platform for Famicom / NES games. The current workload is **Super Mario Bros.** on the pinned Mesen CE fork.

## Authority model

```text
Authoritative Mesen state
        |
        v
SMB1 semantic perception
  - Mario state / capability
  - enemies / clusters
  - terrain / gaps
  - landing-zone hazards
  - rewards
        |
        v
Objective arbitration
  SURVIVE > COLLECT > PROGRESS
        |
        v
Candidate action chunks
        |
        +--> cheap learned ordering / heuristics
        |
        v
Parallel Mesen save-state branches
        |
        v
Event-based trajectory evaluation
        |
        v
Execute a bounded prefix
        |
        +----> observe authoritative state and replan
```

**Mesen is the transition authority.** Python interprets state, proposes objectives and actions, evaluates alternatives, and records evidence. Learned predictions, semantic labels, and shadow rollouts do not replace the live machine state.

## Runtime domains

### Native emulator boundary

The pinned Mesen CE fork provides deterministic frame stepping, direct NES controller state, raw framebuffer access, memory/debugger access, and save/load-state operations. Python reaches these through the native `MesenCore.dll` adapter.

### SMB1 semantic layer

Game-specific code under `src/fami_pixel/games/smb1/` decodes authoritative RAM and machine state into planner-facing semantics. Current modules include observation/events, radar, landing analysis, reward tracking, forward-model evaluation, and live reward interception.

### Objective layer

The live controller treats safety as lexicographically stronger than reward collection, and reward collection as stronger than ordinary progress when a target is active:

```text
SURVIVE > COLLECT > PROGRESS
```

This is an arbitration rule, not machine truth.

### Forward search

Candidate trajectories are evaluated by restoring exact Mesen save states in isolated shadow processes and stepping the real emulator. Relevant event outcomes include death, landing, reward/capability change, level completion, and bounded-horizon unresolved results.

### Receding-horizon execution

The authority executes only a bounded prefix and re-observes the live emulator. A stale asynchronous plan may be rejected or preempted by current-scene safety evidence.

Safety-critical multi-frame maneuvers may hold a commitment across control quanta. Replanning must not restart a jump re-arm sequence or allow a lower-priority stale result to break an already-authorized crossing.

## Observation modes

Fami Pixel supports three research projections without changing the ROM:

- **Vision-only** — native framebuffer → model/policy → action
- **State-only** — RAM / PPU / decoded state → planner/model → action
- **Hybrid** — framebuffer + structured state → planner/model → action

## Evidence discipline

Narrow behavior is validated from deterministic save-state scenarios before another full-level integration run. A full run is integration evidence, not a substitute for local regression gates.

The 2026-09-16 V26 run is the first end-to-end milestone that autonomously completed World 1-1 while also demonstrating live Star collection.

## Documentation rule

> **README explains the system. Issues explain the journey. Code proves the current state.**

Version-by-version debugging history belongs in GitHub issues or `docs/history/`, not in the current architecture contract.
