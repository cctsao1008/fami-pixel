#!/usr/bin/env python3
"""Live Mesen acceptance harness for issue #27 hard-watchdog closure.

This is an intentional fault-injection tool, not a gameplay planner.

It runs the *current V33 live stack* but clamps authoritative controller output to
NOOP only after real World 1-1 entry.  The planner, shadow workers, evidence
recorder, V18 watchdog wrapper, and V23 supervisor/process-lifecycle path remain
otherwise intact.

Expected production behavior under the forced persistent stall:

1. soft watchdog recovery is requested once;
2. the injected controller clamp prevents that recovery from creating progress;
3. the hard threshold is reached from authoritative native-frame / Mario-X data;
4. V18 converts the synthetic terminal evidence to ``watchdog_stall``;
5. the normal live-run bundle is finalized;
6. the V23 supervisor observes watchdog terminal code 8, tears down the current
   authority/shadow process group, and returns;
7. this harness validates the bundle and returns shell code 0 only when all
   acceptance checks pass.

Typical Windows invocation from the repository root::

    py .\tools\smb1_watchdog_hard_acceptance.py .\roms\SuperMarioBros.nes \
      --dll .\build\mesen\MesenCore.dll --shadow-workers 2

A real surrogate artifact may be supplied with ``--surrogate-model``.  If it is
omitted, the harness creates a tiny throwaway compatible model because planner
selection is deliberately rendered irrelevant by the authoritative NOOP clamp.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
EXAMPLES_DIR = ROOT / "examples"
for _path in (SRC_DIR, EXAMPLES_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fami_pixel.learning.tiny_mlp import TinySurrogateMLP
from fami_pixel.runtime.process_lifecycle import authority_pid_from_env, start_parent_lease_monitor
from fami_pixel.telemetry import LiveRunArtifacts

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v17 as v17
import mesen_smb_checkpoint_planner_v18 as v18
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v33 as v33


_ACCEPTANCE_ENV = "FAMI_PIXEL_WATCHDOG_HARD_ACCEPTANCE_RUN"
_ACCEPTANCE_ROOT = ROOT / "build" / "acceptance" / "watchdog-hard"
_EXPECTED_SUPERVISOR_CODE = 8
_REQUIRED_FILES = {
    "summary.json",
    "final-frame.png",
    "final-radar.json",
    "final-state.json",
    "timeline.jsonl",
}


def _acceptance_session_root(token: str) -> Path:
    return (_ACCEPTANCE_ROOT / str(token)).resolve()


def _build_stall_hooks(
    original_enter: Callable,
    original_set_controller: Callable,
    *,
    log: Callable[[str], None] | None = None,
):
    """Return wrappers that arm an authoritative NOOP clamp after game entry."""

    state = {
        "armed": False,
        "controller_writes": 0,
        "nonzero_writes_clamped": 0,
    }

    def enter_world_1_1(*args, **kwargs):
        result = original_enter(*args, **kwargs)
        state["armed"] = True
        if log is not None:
            log(
                "WatchdogAcceptance: World 1-1 entered; authority controller output is now clamped to NOOP"
            )
        return result

    def set_controller(core, port: int, buttons: int):
        state["controller_writes"] += 1
        value = int(buttons)
        if state["armed"]:
            if value != 0:
                state["nonzero_writes_clamped"] += 1
            value = 0
        return original_set_controller(core, port, value)

    return enter_world_1_1, set_controller, state


def _install_authority_stall_injection() -> dict:
    enter, setter, state = _build_stall_hooks(
        base.enter_world_1_1,
        base.set_nes_controller_state,
        log=v11._log,
    )
    base.enter_world_1_1 = enter
    base.set_nes_controller_state = setter
    return state


def _ensure_acceptance_surrogate(args, session_root: Path) -> Path:
    if args.surrogate_model is not None:
        path = args.surrogate_model.expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"surrogate model not found: {path}")
        args.surrogate_model = path
        return path

    # TinySurrogateMLP's current feature contract is 34 inputs.  The generated
    # model is only a compatibility artifact for shadow-process startup; exact
    # planner behavior cannot move the authoritative machine during this test.
    path = session_root / "acceptance-surrogate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    TinySurrogateMLP(input_size=34, hidden_size=16, seed=27).save_json(path)
    args.surrogate_model = path
    v11._log(f"WatchdogAcceptance: generated throwaway surrogate {path}")
    return path


def _install_acceptance_recorder(session_root: Path) -> None:
    def create_recorder():
        try:
            recorder = LiveRunArtifacts(
                root=session_root,
                planner=f"{v17.PLANNER_NAME}-hard-watchdog-acceptance",
            )
            v11._log(f"Run evidence : {recorder.path}")
            return recorder
        except Exception as exc:
            v11._log(f"Run evidence disabled: {type(exc).__name__}: {exc}")
            return None

    v17._create_recorder = create_recorder


def _load_timeline(path: Path) -> list[dict]:
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        rows.append(json.loads(raw))
    return rows


def validate_acceptance_bundle(
    session_root: Path,
    *,
    supervisor_code: int,
) -> dict:
    """Validate the narrow #27 closure contract from persisted evidence."""

    session_root = Path(session_root)
    run_dirs = sorted(path for path in session_root.iterdir() if path.is_dir()) if session_root.is_dir() else []
    run_dir = run_dirs[-1] if run_dirs else None

    checks: dict[str, bool] = {
        "supervisor_reported_watchdog_terminal": int(supervisor_code) == _EXPECTED_SUPERVISOR_CODE,
        "one_evidence_bundle_created": len(run_dirs) == 1,
    }
    details: dict = {
        "supervisor_code": int(supervisor_code),
        "evidence_dir": None if run_dir is None else str(run_dir),
    }

    if run_dir is None:
        return {"passed": False, "checks": checks, "details": details}

    present = {path.name for path in run_dir.iterdir() if path.is_file()}
    checks["normal_evidence_files_present"] = _REQUIRED_FILES.issubset(present)

    summary_path = run_dir / "summary.json"
    timeline_path = run_dir / "timeline.jsonl"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    timeline = _load_timeline(timeline_path) if timeline_path.is_file() else []

    terminal = summary.get("terminal")
    abort_rows = [row for row in timeline if row.get("event") == "watchdog_abort"]
    max_recovery_count = max(
        (int(row.get("watchdog_recovery_count") or 0) for row in timeline),
        default=0,
    )
    max_stagnant_frames = max(
        (int(row.get("watchdog_stagnant_frames") or 0) for row in timeline),
        default=0,
    )

    checks.update(
        {
            "terminal_is_watchdog_stall": terminal == "watchdog_stall",
            "watchdog_abort_timeline_event_present": bool(abort_rows),
            "soft_recovery_preceded_abort": max_recovery_count >= 1,
            "hard_threshold_reached": max_stagnant_frames >= int(v18.WATCHDOG_ABORT_AFTER_FRAMES),
            "final_frame_nonempty": (run_dir / "final-frame.png").is_file()
            and (run_dir / "final-frame.png").stat().st_size > 0,
        }
    )
    details.update(
        {
            "terminal": terminal,
            "planner": summary.get("planner"),
            "timeline_records": len(timeline),
            "watchdog_abort_events": len(abort_rows),
            "max_watchdog_recovery_count": max_recovery_count,
            "max_watchdog_stagnant_frames": max_stagnant_frames,
            "abort_threshold_frames": int(v18.WATCHDOG_ABORT_AFTER_FRAMES),
        }
    )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "details": details,
    }


def _authority_main(args) -> int:
    token = os.environ.get(_ACCEPTANCE_ENV)
    if not token:
        raise SystemExit(f"missing {_ACCEPTANCE_ENV}; use the acceptance supervisor")
    session_root = _acceptance_session_root(token)
    session_root.mkdir(parents=True, exist_ok=True)

    v33._install_v33_overrides()
    _ensure_acceptance_surrogate(args, session_root)
    _install_acceptance_recorder(session_root)
    injection = _install_authority_stall_injection()

    if int(args.max_frames) < int(v18.WATCHDOG_ABORT_AFTER_FRAMES) + 16:
        raise SystemExit(
            "--max-frames is too small for hard-watchdog acceptance; "
            f"need at least {v18.WATCHDOG_ABORT_AFTER_FRAMES + 16}"
        )

    v11._log(
        "WatchdogAcceptance: fault injection armed | "
        f"recover={v18.WATCHDOG_RECOVER_AFTER_FRAMES}f "
        f"abort={v18.WATCHDOG_ABORT_AFTER_FRAMES}f target=V33"
    )
    try:
        return v33.authority_main(args)
    finally:
        v11._log(
            "WatchdogAcceptance: authority controller census | "
            f"writes={injection['controller_writes']} "
            f"nonzero_clamped={injection['nonzero_writes_clamped']}"
        )


def _supervise_acceptance() -> int:
    token = f"{os.getpid()}-{time.time_ns()}"
    session_root = _acceptance_session_root(token)
    previous = os.environ.get(_ACCEPTANCE_ENV)
    os.environ[_ACCEPTANCE_ENV] = token

    # Reuse V23's real supervisor: Windows kill-on-close Job Object, shadow PID
    # census/scrub, checkpoint archive, and watchdog terminal-code mapping.  Only
    # the authority executable path is redirected to this fault-injection wrapper.
    original_v23_file = v23.__file__
    v23.__file__ = str(Path(__file__).resolve())
    try:
        supervisor_code = int(v23.supervise_main())
    finally:
        v23.__file__ = original_v23_file
        if previous is None:
            os.environ.pop(_ACCEPTANCE_ENV, None)
        else:
            os.environ[_ACCEPTANCE_ENV] = previous

    report = validate_acceptance_bundle(
        session_root,
        supervisor_code=supervisor_code,
    )
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    if report["passed"]:
        v11._log(
            "WatchdogAcceptance: PASS | hard stall -> watchdog_stall + normal evidence + clean supervisor return"
        )
        return 0

    v11._log("WatchdogAcceptance: FAIL | see acceptance report above")
    return 1


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        # Normally shadow workers are spawned through V33 after the authority
        # installs the current composition, but keep direct/manual invocation sane.
        v33._install_v33_overrides()
        parent_pid = authority_pid_from_env()
        start_parent_lease_monitor(parent_pid)
        return v23.shadow_worker_main(args)
    if args.authority_worker:
        return _authority_main(args)
    return _supervise_acceptance()


if __name__ == "__main__":
    raise SystemExit(main())
