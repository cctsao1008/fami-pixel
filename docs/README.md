# Fami Pixel Documentation

This directory is organized by **authority**, not by development chronology.

> **README explains the system. Issues explain the journey. Code proves the current state.**

## Current architecture

These documents describe the current durable system boundaries and are the primary documentation authority:

- [System architecture](architecture/system.md)
- [Mesen native integration](architecture/mesen-native-integration.md)
- [SMB1 environment](architecture/smb1-environment.md)
- [SMB1 control semantics](architecture/smb1-control-semantics.md)
- [Live control loop](architecture/live-control-loop.md)
- [Forward-model trajectory planning](architecture/forward-model-trajectory-planning.md)
- [Reward-aware planning](architecture/reward-aware-planning.md)

## Research

These documents describe reusable research methods and model contracts rather than machine authority:

- [SMB1 learned surrogate](research/smb1-surrogate.md)
- [Rollout data and sampling](research/rollout-data.md)

## Operations

- [Windows build and setup](operations/windows-build.md)
- [Checkpoints, evidence, and deterministic replay](operations/checkpoints-and-evidence.md)

## Historical records

`history/` preserves milestone-era documents for provenance. They may describe old planner versions, old validation status, temporary limitations, or superseded paths.

**Historical documents are not authoritative descriptions of current behavior.**

Use them to answer questions such as “how did this design evolve?” rather than “how does fami-pixel work now?”.

## Authority order

When documents disagree, use this order:

```text
current code / tests / runtime evidence
        >
current docs under architecture/, research/, operations/
        >
GitHub issues and experiment records
        >
history/
```

Mesen remains authoritative for emulator transitions and machine state. SMB1 semantics interpret that state. Learned models and planners may propose or rank actions, but they do not redefine machine truth.
