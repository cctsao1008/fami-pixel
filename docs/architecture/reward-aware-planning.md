# Reward-Aware SMB1 Planning

Fami Pixel separates **what must be avoided** from **what is worth pursuing**.

```text
Authoritative Mesen state
        |
        +--> hazard semantics ------+
        |                           |
        +--> reward semantics ------+--> objective arbitration
        |                           |          |
        +--> learned guidance ------+          v
                                      exact Mesen trajectory evaluation
```

Mesen remains authoritative for object state, collision, collection, capability changes, terminal events, and framebuffer output.

## Objective priority

The live controller uses the durable ordering:

```text
SURVIVE > COLLECT > PROGRESS
```

Reward pursuit may justify a bounded detour, but not a trajectory already proven fatal.

## Reward semantics

Current SMB1 reward decoding includes power-up object type and capability state:

```text
Mushroom
Fire Flower
Star
1-Up
```

Collection must be proven by authoritative state transition. Examples:

```text
Mushroom / Fire Flower : player status increases
Star                   : Star invincibility timer increases
```

A visual overlap or heuristic approach score is not collection proof.

## Sticky targets

A collection objective remains sticky for a bounded interval so one corrective frame is not immediately discarded in favor of pure X progress.

The target may be released when it disappears, is collected, becomes unreachable/unsafe, or its bounded objective lifetime expires.

## Interception actions

Reward interception requires more than forward-only movement. The action vocabulary includes short LEFT/RIGHT corrections plus jump release/re-arm/hold chunks so the controller can approach moving or elevated targets.

## Search and live execution

Offline beam search is useful for proving that the action vocabulary can collect a target from a deterministic fixture. The live controller then reuses the composable primitives with short-prefix execution and replanning rather than hard-coding an offline path.

## V26 field evidence

The successful 2026-09-16 World 1-1 run demonstrated live Star interception with an authoritative capability transition:

```text
Star invincibility timer: 0 -> positive
```

The run then continued navigation and completed the level.

## Boundary

Reward utility is policy. Collection and capability state are machine evidence. These must remain separate in code, telemetry, and documentation.
