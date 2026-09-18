from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def _load_v27():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v27.py"
        spec = spec_from_file_location("planner_v27_authority_runtime_wiring", path)
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


class _Core:
    def __init__(self, frame: int):
        self._frame = int(frame)

    def frame_count(self) -> int:
        return self._frame


def test_v27_authority_scope_records_port0_and_restores_predecessor(monkeypatch):
    v27 = _load_v27()
    base_calls = []
    core = _Core(123)

    def base_setter(call_core, port, buttons):
        base_calls.append((call_core, int(port), int(buttons)))
        return "base-result"

    monkeypatch.setattr(v27.base, "set_nes_controller_state", base_setter)
    monkeypatch.setattr(v27.v11, "_log", lambda *_args, **_kwargs: None)

    def delegated(_args):
        installed = v27.base.set_nes_controller_state
        assert installed is not base_setter
        assert installed(core, 0, 0x82) == "base-result"
        assert installed(core, 1, 0x40) == "base-result"
        return 27

    monkeypatch.setattr(v27.v26, "authority_main", delegated)

    assert v27.authority_main(SimpleNamespace()) == 27
    assert base_calls == [
        (core, 0, 0x82),
        (core, 1, 0x40),
    ]
    assert v27._AUTHORITY_ACTION_LEDGER.buttons_between(123, 124) == (0x82,)
    assert v27.base.set_nes_controller_state is base_setter


def test_v27_authority_scope_restores_predecessor_on_delegate_error(monkeypatch):
    v27 = _load_v27()

    def base_setter(core, port, buttons):
        return (core, port, buttons)

    monkeypatch.setattr(v27.base, "set_nes_controller_state", base_setter)
    monkeypatch.setattr(v27.v11, "_log", lambda *_args, **_kwargs: None)

    def delegated(_args):
        installed = v27.base.set_nes_controller_state
        assert installed is not base_setter
        installed(_Core(77), 0, 0x80)
        raise RuntimeError("delegated authority failed")

    monkeypatch.setattr(v27.v26, "authority_main", delegated)

    with pytest.raises(RuntimeError, match="delegated authority failed"):
        v27.authority_main(SimpleNamespace())

    assert v27.base.set_nes_controller_state is base_setter
    assert v27._AUTHORITY_ACTION_LEDGER.buttons_between(77, 78) == (0x80,)
