# Mesen Native Integration

This document defines the durable native boundary between Python and the pinned Mesen CE fork.

## Build artifact

Mesen CE is pinned under:

```text
modules/mesen
```

The native interop project builds `MesenCore.dll`, staged by the repository wrapper at:

```text
build/mesen/MesenCore.dll
```

Use `tools/build_mesen.ps1`; do not substitute an arbitrary stock DLL that lacks the fami-pixel native additions.

## Headless lifecycle

The validated lifecycle uses native interop for initialization, ROM load, debugger/runtime control, and teardown. ROMs remain user-provided local files and are never repository content.

## Deterministic frame stepping

Fami Pixel requires a real frame boundary, not wall-clock sleeps.

Repeated debugger stepping cannot treat `IsExecutionStopped()` alone as proof that a new frame ran; the debugger may already be stopped at the previous boundary. The project therefore uses its native frame-step path and independent frame witnesses rather than a sleep/poll approximation.

`ResumeExecution()` must not be blindly appended after a newly installed PPU-frame step request because the debugger run path can replace the request.

## Direct NES input

The fork exposes a narrow NES input-provider path rather than relying on virtual HID or desktop automation.

Controller byte layout:

```text
bit 0  A
bit 1  B
bit 2  Select
bit 3  Start
bit 4  Up
bit 5  Down
bit 6  Left
bit 7  Right
```

Examples:

```text
RELEASE     0x00
A           0x01
B           0x02
START       0x08
LEFT        0x40
RIGHT       0x80
RIGHT+B     0x82
RIGHT+A+B   0x83
```

The generic native adapter owns emulator-level button delivery. SMB-specific RAM encodings remain above this layer.

## Native NES framebuffer

The canonical NES frame is the emulator-owned 256×240 PPU output, not a Windows screen capture.

Mesen internally owns alternating output buffers. Fami Pixel therefore copies the frame into caller-owned memory while execution is stopped rather than exposing a raw pointer whose ownership role changes on the next frame.

The raw NES frame contract is:

```text
width        256
height       240
storage      uint16_t per pixel
row order    row-major
```

Color conversion belongs above the native adapter.

## RAM / debugger access

The adapter exposes the verified memory/debugger surface required by the environment. Struct- or enum-heavy APIs are bound only after their pinned declarations and layout are audited.

## Save states

Save/load-state operations are machine checkpoints. They are used for deterministic regression roots and exact shadow rollouts.

A counterfactual branch is never authoritative merely because it used a real Mesen state. Only the live authority trajectory produces durable game events.

## Process isolation

Counterfactual save/load rollouts run in separate Mesen processes and isolated runtime homes. The authoritative Mesen instance never restores a planner branch.

On Windows, supervisors may use process-tree containment so a terminal run returns control even if native teardown hangs.

## Non-goals

The production control path does not depend on:

```text
Lua
Windows screen scraping
virtual gamepads
GUI automation
ROM modification
```
