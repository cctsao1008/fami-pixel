import pytest

from fami_pixel.control import AuthorityRuntimeScope, authority_action_recording_layer
from fami_pixel.games.smb1.action_lineage import AuthorityActionLedger


def test_authority_runtime_scope_preserves_reset_order():
    calls = []
    holder = {"setter": lambda *_args: None}
    scope = AuthorityRuntimeScope(
        get_controller_setter=lambda: holder["setter"],
        install_controller_setter=lambda setter: holder.__setitem__("setter", setter),
        resetters=(
            lambda: calls.append("collect"),
            lambda: calls.append("lineage"),
            lambda: calls.append("gap"),
        ),
    )

    scope.reset_run_state()

    assert calls == ["collect", "lineage", "gap"]


def test_controller_layer_restores_exact_predecessor_after_error():
    calls = []

    def base(core, port, buttons):
        calls.append(("base", core, port, buttons))
        return "base-result"

    holder = {"setter": base}
    scope = AuthorityRuntimeScope(
        get_controller_setter=lambda: holder["setter"],
        install_controller_setter=lambda setter: holder.__setitem__("setter", setter),
    )

    def capture_layer(original):
        def capture(core, port, buttons):
            calls.append(("capture", core, port, buttons))
            return original(core, port, buttons)

        return capture

    with pytest.raises(RuntimeError, match="boom"):
        with scope.controller_layer(capture_layer):
            assert holder["setter"]("core", 0, 0x82) == "base-result"
            raise RuntimeError("boom")

    assert holder["setter"] is base
    assert calls == [
        ("capture", "core", 0, 0x82),
        ("base", "core", 0, 0x82),
    ]


def test_nested_controller_layers_preserve_v35_v27_installation_semantics():
    calls = []

    def base(core, port, buttons):
        calls.append("base")
        return buttons

    holder = {"setter": base}
    scope = AuthorityRuntimeScope(
        get_controller_setter=lambda: holder["setter"],
        install_controller_setter=lambda setter: holder.__setitem__("setter", setter),
    )

    def capture_layer(original):
        def capture(core, port, buttons):
            calls.append("capture")
            return original(core, port, buttons)

        return capture

    def lineage_layer(original):
        def record(core, port, buttons):
            calls.append("lineage")
            return original(core, port, buttons)

        return record

    with scope.controller_layer(capture_layer):
        capture_setter = holder["setter"]
        with scope.controller_layer(lineage_layer):
            assert holder["setter"]("core", 0, 0x81) == 0x81
            assert calls == ["lineage", "capture", "base"]
        assert holder["setter"] is capture_setter
    assert holder["setter"] is base


def test_authority_action_recording_layer_matches_v27_port0_and_suspension_contract():
    class Core:
        frame = 42

        def frame_count(self):
            return self.frame

    core = Core()
    ledger = AuthorityActionLedger(max_entries=8)
    calls = []

    def base(current_core, port, buttons):
        calls.append((current_core.frame_count(), port, buttons))
        return buttons

    holder = {"setter": base}
    scope = AuthorityRuntimeScope(
        get_controller_setter=lambda: holder["setter"],
        install_controller_setter=lambda setter: holder.__setitem__("setter", setter),
    )

    with scope.controller_layer(authority_action_recording_layer(ledger)):
        assert holder["setter"](core, 0, 0x81) == 0x81
        assert ledger.buttons_between(42, 43) == (0x81,)

        core.frame = 43
        assert holder["setter"](core, 1, 0x40) == 0x40
        assert ledger.buttons_between(43, 44) is None

        with ledger.suspend_recording():
            assert holder["setter"](core, 0, 0x82) == 0x82
        assert ledger.buttons_between(43, 44) is None

    assert holder["setter"] is base
    assert calls == [(42, 0, 0x81), (43, 1, 0x40), (43, 0, 0x82)]


def test_authority_action_recording_layer_rejects_non_ledger_dependency():
    with pytest.raises(TypeError, match="record"):
        authority_action_recording_layer(object())
