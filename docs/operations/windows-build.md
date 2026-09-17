# Windows Fresh Clone, Toolchain, Build, and Run Guide

This is the current end-to-end setup path for bringing **fami-pixel** up on a new Windows x64 machine from a clean checkout.

It covers:

```text
clone repository + pinned Mesen submodule
        ->
create Python environment
        ->
install/provision native C++ toolchain
        ->
build fami-pixel MesenCore.dll
        ->
run ABI + pytest validation
        ->
run a ROM smoke test
        ->
run the current live SMB1 planner
```

The repository is:

```text
https://github.com/cctsao1008/fami-pixel
```

The native emulator dependency is pinned as a Git submodule:

```text
modules/mesen
-> https://github.com/cctsao1008/MesenCE.git
```

## 1. Platform and prerequisites

Recommended environment:

```text
Windows 10/11 x64
PowerShell
Git for Windows
Python 3.11 or newer
MSVC + MSBuild + Windows SDK
```

Verify the basic tools first:

```powershell
git --version
py --version
```

The Python package currently requires:

```text
Python >= 3.11
```

For the native compiler toolchain, choose either:

```text
A. repository-provisioned portable toolchain   <- recommended for reproducibility
B. installed Visual Studio / Build Tools       <- normal workstation setup
```

Do not install both unless you specifically need both workflows.

## 2. Clone the repository

Example workspace:

```powershell
D:
mkdir my-github -ErrorAction SilentlyContinue
cd D:\my-github

git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel
```

Verify the checkout:

```powershell
git status
git submodule status
```

The following files must exist:

```text
modules\mesen\Mesen.sln
modules\mesen\InteropDLL\InteropDLL.vcxproj
```

If the repository was cloned without `--recurse-submodules`, repair it with:

```powershell
git submodule sync --recursive
git submodule update --init --recursive
```

### Optional: work on another repository branch

The default checkout is `main`.

If development is currently happening on another branch, switch to it before building, then resynchronize the submodule:

```powershell
git fetch --all --prune
git switch <branch-name>
git submodule sync --recursive
git submodule update --init --recursive
```

Do not manually move `modules/mesen` to an arbitrary commit. The parent repository's pinned gitlink is the native-source authority for that checkout.

## 3. Create the Python environment

From the repository root:

```powershell
.\tools\setup_python_env.ps1
```

The bootstrap script:

```text
checks Python >= 3.11
creates .venv when missing
upgrades pip/setuptools
installs fami-pixel in editable mode
installs the declared test dependency group
verifies import fami_pixel
verifies pytest availability
```

Activate the environment in the current PowerShell if desired:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activation is optional. For deterministic commands in this guide, the repo-local interpreter can be used directly:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import fami_pixel; print(fami_pixel.__file__)"
```

### PowerShell execution-policy failure

If local PowerShell policy blocks repository scripts, use a process-local bypass rather than changing the machine permanently:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then rerun the repository script.

## 4. Install/provision the native build toolchain

### Option A — portable toolchain (recommended for reproducibility)

The repository owns a provisioning wrapper:

```text
tools\setup_portable_vs.ps1
```

It checks out a pinned revision of `reksar/vsget` and provisions the MSVC compiler, MSBuild, Visual C++ targets, and Windows SDK into the requested directory.

Example:

```powershell
.\tools\setup_portable_vs.ps1 -Destination D:\PortableVS
```

The wrapper validates the resulting tree, including:

```text
cl.exe
MSBuild.exe
Microsoft.Cpp.Default.props
Microsoft.Cpp.props
Microsoft.Cpp.targets
Windows SDK libraries/binaries
winrt\wrl.h
winrt\wrl\client.h
```

No permanent PATH or registry changes are required by the fami-pixel wrapper.

Re-running the provisioning command is safe: if the destination already contains a complete supported layout, the script validates it and skips reprovisioning unless `-ForceProvision` is supplied.

### Option B — installed Visual Studio / Build Tools

Install Visual Studio or Visual Studio Build Tools with the equivalent of:

```text
Desktop development with C++
MSVC x64/x86 build tools
MSBuild
Windows 10/11 SDK
```

The fami-pixel build wrapper attempts to locate `MSBuild.exe` from `PATH` or through `vswhere.exe`.

## 5. Build MesenCore.dll

The canonical native build wrapper is:

```text
tools\build_mesen.ps1
```

It performs a clean `Release|x64` build of the pinned Mesen `InteropDLL` target through `Mesen.sln`, stages the resulting DLL, and then runs the fami-pixel ABI/export probe.

### Portable toolchain

Recommended command on a fresh machine:

```powershell
.\tools\build_mesen.ps1 -PortableVS D:\PortableVS -LowMemory
```

`-LowMemory` limits both MSBuild and compiler parallelism:

```text
MSBuild /m:1
CL_MPCount=1
```

This trades build speed for lower peak memory/commit usage and is a good default on an unfamiliar workstation.

### Installed Visual Studio / Build Tools

```powershell
.\tools\build_mesen.ps1
```

### Expected artifact

A successful build stages:

```text
build\mesen\MesenCore.dll
```

Verify:

```powershell
Test-Path .\build\mesen\MesenCore.dll
```

Expected:

```text
True
```

The build wrapper suppresses only MSVC warning `C4819` for the pinned upstream source tree. Do not globally force `/utf-8` as a workaround for source-code-page warnings.

Do not replace the generated DLL with a stock upstream Mesen CE DLL. fami-pixel depends on native interop extensions for deterministic frame stepping, framebuffer/state access, controller override, and save-state-based execution.

## 6. Validate the native ABI manually

The build wrapper normally runs this automatically.

To rerun it explicitly:

```powershell
.\.venv\Scripts\python.exe .\tools\inspect_mesen_exports.py .\build\mesen\MesenCore.dll
```

If this probe fails, do not continue into planner debugging yet. Fix the native build/submodule/toolchain mismatch first.

## 7. Run the repository test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Do not depend on a hard-coded pass count in documentation. The current checkout's actual test result is authoritative.

At this point the following should all be true:

```text
Git checkout clean or intentionally modified
Mesen submodule initialized at the pinned commit
Python environment imports fami_pixel
build\mesen\MesenCore.dll exists
ABI probe passes
pytest passes
```

## 8. Provide the ROM locally

ROMs are external user-provided files and are not stored in this repository.

Use a legally obtained local Super Mario Bros. ROM, for example:

```text
D:\roms\Super Mario Bros. (Japan, USA).nes
```

Do not commit ROM images into fami-pixel.

For the following commands:

```powershell
$ROM = "D:\roms\Super Mario Bros. (Japan, USA).nes"
```

## 9. First runtime smoke test

Before running the full planner, verify the complete native path with the stable gameplay-entry smoke test:

```powershell
.\.venv\Scripts\python.exe .\examples\mesen_smb_gameplay_entry.py $ROM
```

This validates, through native Mesen state:

```text
DLL load
headless Mesen initialization
NES controller configuration
ROM load
Mesen debugger initialization
START input
entry into SMB1 World 1-1
RIGHT movement
A-button jump
controller release
```

Typical successful output includes lines such as:

```text
Init      : PASS
NesConfig : PASS
LoadRom   : PASS
Debugger  : PASS
TitleMenu : PASS
GameEntry : PASS
Movement  : PASS
Jump      : PASS
Release   : PASS
Gameplay  : PASS
Supervisor: PASS
```

If this smoke test fails, fix the environment before moving to the concurrent planner.

## 10. Generated model artifacts on a new machine

A fresh Git clone does **not** contain local generated research artifacts under `build/`.

That includes trained surrogate models and rollout datasets.

The current planner stack can use/require a surrogate model for candidate ranking/progress search. A typical model path is:

```text
build\models\smb1-surrogate-vNNN\model.json
```

On a new PC, use one of these approaches:

```text
1. Copy the reviewed model artifact from the previous machine.
2. Restore it from your own artifact backup.
3. Retrain it if the required rollout JSONL datasets are also available.
```

Do not assume that cloning the Git repository reconstructs generated `build/` artifacts.

For training details, see:

```text
docs\research\smb1-surrogate.md
```

Example variable after a model is available:

```powershell
$MODEL = ".\build\models\smb1-surrogate-v001\model.json"
```

## 11. Run the current live SMB1 planner

Planner scripts are versioned because the control policy is under active research.

At the time this guide was updated, `main` contains V33 as the newest development planner, while the repository README documents V26 as the demonstrated World 1-1 milestone.

Run V33 with a valid model artifact:

```powershell
.\.venv\Scripts\python.exe .\examples\mesen_smb_checkpoint_planner_v33.py $ROM `
  --surrogate-model $MODEL `
  --shadow-workers 4 `
  --web-ui
```

The important runtime architecture is:

```text
live Mesen authority
        |
        v
SMB1 state / radar observation
        |
        v
objective selection
        |
        v
candidate trajectories
        |
        v
parallel exact-Mesen shadow workers
        |
        v
short execution prefix
        |
        v
re-observe live authority and replan
```

The learned surrogate is proposal/ranking guidance. Exact Mesen transitions remain machine authority.

### Historical demonstrated controller

To reproduce the README's V26 milestone controller instead:

```powershell
.\.venv\Scripts\python.exe .\examples\mesen_smb_checkpoint_planner_v26.py $ROM `
  --surrogate-model $MODEL `
  --shadow-workers 4 `
  --web-ui
```

## 12. Typical local generated directories

After setup/build/runtime work, a workstation may contain:

```text
fami-pixel\
├─ .venv\
├─ build\
│  ├─ mesen\
│  │  └─ MesenCore.dll
│  ├─ mesen-home\
│  ├─ checkpoints\
│  ├─ live-runs\
│  ├─ datasets\
│  └─ models\
├─ modules\
│  └─ mesen\
├─ examples\
├─ src\
├─ tests\
└─ tools\
```

`.venv\` and generated `build\` content are local machine artifacts and should remain outside normal source-control history.

## 13. Update an existing checkout

Use this sequence rather than updating the parent repository alone:

```powershell
cd D:\my-github\fami-pixel

git pull
git submodule sync --recursive
git submodule update --init --recursive

.\tools\setup_python_env.ps1
```

Rebuild `MesenCore.dll` whenever the pinned Mesen submodule or native interop code changes:

```powershell
.\tools\build_mesen.ps1 -PortableVS D:\PortableVS -LowMemory
```

Then rerun:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 14. Fast-path checklist — portable toolchain

For a completely new Windows machine with Git and Python already installed:

```powershell
# Clone source + pinned Mesen submodule
git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel

# Python environment
.\tools\setup_python_env.ps1

# Provision reproducible portable MSVC/MSBuild/SDK toolchain
.\tools\setup_portable_vs.ps1 -Destination D:\PortableVS

# Build native Mesen interop
.\tools\build_mesen.ps1 -PortableVS D:\PortableVS -LowMemory

# Validate repository
.\.venv\Scripts\python.exe -m pytest -q

# Set local ROM path
$ROM = "D:\roms\Super Mario Bros. (Japan, USA).nes"

# End-to-end smoke test
.\.venv\Scripts\python.exe .\examples\mesen_smb_gameplay_entry.py $ROM
```

After that, provide/restore the local surrogate model artifact and run the current planner.

## 15. Fast-path checklist — installed Visual Studio

```powershell
# Clone source + pinned Mesen submodule
git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel

# Python environment
.\tools\setup_python_env.ps1

# Build using installed Visual Studio / Build Tools
.\tools\build_mesen.ps1

# Validate repository
.\.venv\Scripts\python.exe -m pytest -q

# Smoke test
$ROM = "D:\roms\Super Mario Bros. (Japan, USA).nes"
.\.venv\Scripts\python.exe .\examples\mesen_smb_gameplay_entry.py $ROM
```

## Troubleshooting

### `Mesen CE submodule is not initialized`

```powershell
git submodule sync --recursive
git submodule update --init --recursive
git submodule status
```

### `MSBuild.exe was not found`

Either install Visual Studio / Build Tools with C++ support, or provision the repository-supported portable toolchain:

```powershell
.\tools\setup_portable_vs.ps1 -Destination D:\PortableVS
.\tools\build_mesen.ps1 -PortableVS D:\PortableVS -LowMemory
```

### Missing `wrl.h` or `wrl/client.h`

Re-run the portable provisioning validator:

```powershell
.\tools\setup_portable_vs.ps1 -Destination D:\PortableVS
```

The repository checks for the Windows SDK WinRT/WRL headers explicitly.

### `C3859`, `C1076`, `C1060`, PCH exhaustion, or paging-file pressure

Retry with low-memory mode:

```powershell
.\tools\build_mesen.ps1 -PortableVS D:\PortableVS -LowMemory
```

### `MesenCore.dll` is missing

Expected path:

```text
build\mesen\MesenCore.dll
```

Re-run the native build wrapper rather than copying an unrelated DLL into the directory.

### Python cannot import `fami_pixel`, or pytest is missing

```powershell
.\tools\setup_python_env.ps1
.\.venv\Scripts\python.exe -c "import fami_pixel"
.\.venv\Scripts\python.exe -m pytest --version
```

### Planner fails but the smoke test passes

Check generated runtime dependencies first:

```text
valid surrogate model path
correct planner arguments
sufficient worker resources
current checkout / matching submodule
```

Then preserve the exact runtime log and generated run evidence.

### Orphaned shadow workers remain after an interrupted run

Inspect or clean them with the repository tools:

```powershell
.\tools\inspect_fami_pixel_processes.ps1
.\tools\cleanup_orphan_shadow_workers.ps1
```

### `FamiPixelStepFrame failed (... timeout waiting for native frame advance)`

Treat this as a native synchronization failure, not as a normal condition to hide by indefinitely increasing timeouts.

Verify in this order:

```text
1. pinned Mesen submodule is correct
2. MesenCore.dll was freshly rebuilt
3. ABI probe passes
4. pytest passes
5. gameplay-entry smoke test passes
6. preserve the exact failing runtime log
```

## Authority rules

```text
Git checkout
    = source/code/config authority

Pinned modules/mesen gitlink
    = native emulator source authority for this checkout

Built Mesen runtime
    = machine-state / transition authority

Python environment + planner
    = experiment, semantics, and control layer

Generated model artifacts
    = local proposal/ranking research artifacts

Local ROM
    = user-provided external game image
```

When planner assumptions disagree with direct Mesen evidence, Mesen wins.
