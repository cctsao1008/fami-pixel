import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_acceptance_module():
    root = Path(__file__).resolve().parents[1]
    tools = root / "tools"
    path = tools / "smb1_watchdog_hard_acceptance.py"
    spec = spec_from_file_location("watchdog_hard_acceptance", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_stall_injection_arms_only_after_real_game_entry():
    acceptance = _load_acceptance_module()
    writes = []
    logs = []

    def enter(*args, **kwargs):
        return {"entered": True}

    def set_controller(core, port, buttons):
        writes.append((core, port, buttons))
        return buttons

    wrapped_enter, wrapped_set, state = acceptance._build_stall_hooks(
        enter,
        set_controller,
        log=logs.append,
    )

    # Boot/title-menu input must pass through untouched.
    assert wrapped_set("core", 0, 0x82) == 0x82
    assert writes[-1][2] == 0x82
    assert state["armed"] is False

    assert wrapped_enter("core", 5.0) == {"entered": True}
    assert state["armed"] is True
    assert logs and "clamped to NOOP" in logs[-1]

    # Once World 1-1 is entered, all authority commands are physically clamped.
    assert wrapped_set("core", 0, 0x82) == 0
    assert writes[-1][2] == 0
    assert wrapped_set("core", 0, 0x00) == 0
    assert state["controller_writes"] == 3
    assert state["nonzero_writes_clamped"] == 1


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_acceptance_bundle_requires_watchdog_terminal_soft_recovery_and_hard_abort(tmp_path):
    acceptance = _load_acceptance_module()
    session = tmp_path / "session"
    run = session / "run"
    run.mkdir(parents=True)

    _write_json(
        run / "summary.json",
        {
            "terminal": "watchdog_stall",
            "planner": "v33-authority-observed-collect-deadline",
        },
    )
    _write_json(run / "final-radar.json", {})
    _write_json(run / "final-state.json", {"mario_x": 40})
    (run / "final-frame.png").write_bytes(b"png")
    timeline = [
        {
            "watchdog_state": "recovery",
            "watchdog_stagnant_frames": 120,
            "watchdog_recovery_count": 1,
        },
        {
            "event": "watchdog_abort",
            "watchdog_state": "abort",
            "watchdog_stagnant_frames": acceptance.v18.WATCHDOG_ABORT_AFTER_FRAMES,
            "watchdog_recovery_count": 1,
        },
    ]
    (run / "timeline.jsonl").write_text(
        "\n".join(json.dumps(row) for row in timeline) + "\n",
        encoding="utf-8",
    )

    report = acceptance.validate_acceptance_bundle(
        session,
        supervisor_code=8,
    )
    assert report["passed"] is True
    assert all(report["checks"].values())
    assert report["details"]["terminal"] == "watchdog_stall"
    assert report["details"]["max_watchdog_recovery_count"] == 1


def test_acceptance_bundle_rejects_plain_death_even_with_files(tmp_path):
    acceptance = _load_acceptance_module()
    session = tmp_path / "session"
    run = session / "run"
    run.mkdir(parents=True)

    _write_json(run / "summary.json", {"terminal": "death"})
    _write_json(run / "final-radar.json", {})
    _write_json(run / "final-state.json", {})
    (run / "final-frame.png").write_bytes(b"png")
    (run / "timeline.jsonl").write_text(
        json.dumps(
            {
                "event": "watchdog_abort",
                "watchdog_stagnant_frames": acceptance.v18.WATCHDOG_ABORT_AFTER_FRAMES,
                "watchdog_recovery_count": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = acceptance.validate_acceptance_bundle(session, supervisor_code=8)
    assert report["passed"] is False
    assert report["checks"]["terminal_is_watchdog_stall"] is False
