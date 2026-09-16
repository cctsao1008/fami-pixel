# SMB1 Environment Contract

The SMB1 environment is a projection above authoritative Mesen execution.

```text
Mesen machine state
      ↓
SMB1 decoder / semantic layer
      ↓
observation + events + radar + reward state
      ↓
planner / learning / evidence
```

Machine authority remains in Mesen. Game interpretation belongs in the SMB1 layer. Policy and learning remain above both.

## Observation

The durable structured observation includes machine- and game-state fields such as:

```text
native_frame_id
smb_frame_counter
world
level
mario_x_abs
mario_y
mario_y_high
player_state
player_x_speed
player_y_speed
raw_joypad
operating mode / task
engine subroutine
```

Framebuffer data may be attached for visual or hybrid policies, but the environment does not force image inference for state already available authoritatively from RAM.

## Actions

The SMB1 action vocabulary exposes frame-explicit NES controller combinations rather than a fixed catalog of semantic jump names:

```text
NOOP
A
B
RIGHT
RIGHT_A
RIGHT_B
RIGHT_A_B
LEFT
LEFT_A
LEFT_B
LEFT_A_B
```

A command is controller state plus duration in emulator frames. Higher-level planners may compose short schedules.

## Terminal events

Terminal semantics are derived from SMB1 engine state and the live Mesen trajectory.

Observed authoritative paths include:

```text
enemy/collision death : entry into engine 0x0B
pit/fall death        : direct entry into engine 0x06 from active play
level completion      : entry into engine 0x05
```

The later `0x0B -> 0x06` bookkeeping transition must not emit a duplicate death.

`0x04` flagpole slide is a completion precursor, not the terminal event itself.

## Semantic radar

Current-scene perception includes:

- hostile enemy positions and clusters;
- terrain/gap evidence;
- landing-zone enemy pressure;
- reward objects and capability state.

Semantic labels are planning evidence, not machine truth. In particular, absence of positive gap evidence must not silently become proof of safe terrain.

## Rewards and capabilities

Reward state is decoded from live game state. Current power-up semantics include Mushroom, Fire Flower, Star, and 1-Up object typing, plus player capability state and Star invincibility timer.

Collection proof must come from authoritative state transitions, for example:

```text
Mushroom / Fire Flower : player status rises
Star                   : invincibility timer rises
```

A reward target may justify a bounded detour only when safety remains acceptable.

## Grounding / support

Grounding is not equivalent to `Y >= 160`. Elevated surfaces exist. Live support decisions use player state, vertical state, and machine evidence rather than a single screen-Y threshold.

## Traceability

Every live transition should remain reconstructable from evidence containing at least:

```text
frame / generation
logical action and NES byte
Mario state
engine state
semantic radar
objective / target
planner source and age
terminal outcome
```

The environment is a reproducible interpretation layer, not a hidden policy engine.
