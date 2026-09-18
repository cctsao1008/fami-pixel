import pytest

from fami_pixel.control import AuthorityRuntimeScope


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
