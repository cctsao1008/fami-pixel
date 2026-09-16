# Windows Build and Setup

This is the current setup path for a clean fami-pixel checkout on Windows.

## Prerequisites

- Git
- Python 3.11+
- Visual Studio / Build Tools with C++ support, **or** a complete portable MSVC/MSBuild/Windows SDK tree

## Clone

```powershell
git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel
```

If needed:

```powershell
git submodule update --init --recursive
```

The pinned Mesen fork lives at `modules/mesen`.

## Python environment

```powershell
.\tools\setup_python_env.ps1
.\.venv\Scripts\Activate.ps1
```

The script creates the repo-local virtual environment, installs the package in editable mode, installs declared test dependencies, and verifies imports.

## Build MesenCore.dll

Installed Visual Studio / Build Tools:

```powershell
.\tools\build_mesen.ps1
```

Portable toolchain example:

```powershell
.\tools\build_mesen.ps1 -PortableVS E:\PortableVS -LowMemory
```

`-LowMemory` reduces MSBuild/compiler parallelism and is useful on constrained machines.

Expected artifact:

```text
build\mesen\MesenCore.dll
```

The wrapper also runs the native ABI/export probe. Do not replace the result with a stock Mesen CE DLL that lacks fami-pixel native extensions.

## Tests

Run the repository test suite from the repo root:

```powershell
python -m pytest -q
```

Do not encode a permanent pass-count in documentation; the current checkout's actual result is authoritative.

## ROMs

ROMs are external user-provided files and are never committed. Use a legally obtained local SMB1 ROM path for Mesen-based examples.

## Current live example

The current integration entry point is the V26 controller:

```powershell
py .\examples\mesen_smb_checkpoint_planner_v26.py `
  "D:\path\to\Super Mario Bros. (Japan, USA).nes" `
  --surrogate-model .\build\models\smb1-tiny-risk.json `
  --shadow-workers 4 `
  --web-ui
```

The learned model artifact is a generated local research artifact; create or supply it before using that option.

## Local generated directories

Typical generated content includes:

```text
.venv\
build\mesen\
build\mesen-home\
build\checkpoints\
build\live-runs\
build\datasets\
build\models\
```

These are local runtime/research artifacts and should remain ignored by Git.

## Troubleshooting priorities

If native stepping/build/runtime fails, first verify:

```text
correct pinned submodule
fresh MesenCore.dll build
ABI probe passes
pytest passes
ROM path is valid
```

Preserve the exact runtime log and evidence bundle rather than hiding synchronization failures by increasing timeouts indefinitely.
