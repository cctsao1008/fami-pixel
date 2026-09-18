from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_authority_runtime_wiring", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        return module
    finally:
        try:
            sys.path.remove(str(examples))
        except ValueError:
            pass


def _args(tmp_path):
    return SimpleNamespace(
        step_timeout=1.25,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )


def _disable_outer_runtime_side_effects(monkeypatch, v35):
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_RESET_PLAN",
        SimpleNamespace(
            reset_through=lambda _name: None,
            reset_named=lambda _name: None,
        ),
    )
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: None),
    )


def test_v35_authority_scope_captures_live_core_and_restores_predecessor(monkeypatch, tmp_path):
    v35 = _load_v35()
    base_calls = []
    captured = {}

    def base_setter(core, port, buttons):
        base_calls.append((core, int(port), int(buttons)))
        return "base-result"

    monkeypatch.setattr(v35.base, "set_nes_controller_state", base_setter)
    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    _disable_outer_runtime_side_effects(monkeypatch, v35)

    def delegated(_args):
        installed = v35.base.set_nes_controller_state
        assert installed is not base_setter
        core = object()
        captured["core"] = core
        assert installed(core, 0, 0x82) == "base-result"
        assert v35._LIVE_AUTHORITY_CORE is core
        return 17

    monkeypatch.setattr(v35._V28, "authority_main", delegated)

    assert v35.authority_main(_args(tmp_path)) == 17
    assert base_calls == [(captured["core"], 0, 0x82)]
    assert v35.base.set_nes_controller_state is base_setter
    assert v35._LIVE_AUTHORITY_CORE is None
    assert v35._SYNC_STEP_TIMEOUT == 1.25
    assert v35._SYNC_CHECKPOINT == (tmp_path / "checkpoints" / "v35-sync-star-current.mss").resolve()


def test_v35_authority_scope_restores_controller_and_live_core_on_delegate_error(monkeypatch, tmp_path):
    v35 = _load_v35()

    def base_setter(core, port, buttons):
        return (core, port, buttons)

    monkeypatch.setattr(v35.base, "set_nes_controller_state", base_setter)
    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    _disable_outer_runtime_side_effects(monkeypatch, v35)

    def delegated(_args):
        installed = v35.base.set_nes_controller_state
        core = object()
        installed(core, 0, 0x80)
        assert v35._LIVE_AUTHORITY_CORE is core
        raise RuntimeError("delegated authority failed")

    monkeypatch.setattr(v35._V28, "authority_main", delegated)

    with pytest.raises(RuntimeError, match="delegated authority failed"):
        v35.authority_main(_args(tmp_path))

    assert v35.base.set_nes_controller_state is base_setter
    assert v35._LIVE_AUTHORITY_CORE is None
