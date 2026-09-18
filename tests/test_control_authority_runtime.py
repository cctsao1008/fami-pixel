import pytest

from fami_pixel.control import (
    AuthorityRunResetPlan,
    AuthorityRunSetupPlan,
    AuthorityRuntimeScope,
    NamedRunReset,
    NamedRunSetup,
    authority_action_recording_layer,
)
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


def test_authority_run_reset_plan_preserves_named_order_and_duplicate_targets():
    calls = []

    def clear_collect():
        calls.append("collect")

    plan = AuthorityRunResetPlan(
        steps=(
            NamedRunReset("v32-collect", clear_collect),
            NamedRunReset("v29-collect", clear_collect),
            NamedRunReset("lineage", lambda: calls.append("lineage")),
        )
    )

    assert plan.names == ("v32-collect", "v29-collect", "lineage")
    plan.reset_run_state()
    assert calls == ["collect", "collect", "lineage"]


def test_authority_run_reset_plan_can_transfer_only_an_ordered_prefix():
    calls = []
    plan = AuthorityRunResetPlan(
        steps=(
            NamedRunReset("outer-a", lambda: calls.append("a")),
            NamedRunReset("outer-b", lambda: calls.append("b")),
            NamedRunReset("inner", lambda: calls.append("inner")),
        )
    )

    plan.reset_through("outer-b")

    assert calls == ["a", "b"]


def test_authority_run_reset_plan_validates_prefix_target_before_side_effects():
    calls = []
    plan = AuthorityRunResetPlan(
        steps=(NamedRunReset("known", lambda: calls.append("known")),)
    )

    with pytest.raises(KeyError, match="unknown"):
        plan.reset_through("missing")

    assert calls == []


def test_authority_run_reset_plan_rejects_duplicate_step_names():
    reset = lambda: None
    with pytest.raises(ValueError, match="unique"):
        AuthorityRunResetPlan(
            steps=(
                NamedRunReset("same", reset),
                NamedRunReset("same", reset),
            )
        )


def test_authority_run_setup_plan_preserves_order_and_returns_named_results():
    calls = []
    args = object()
    plan = AuthorityRunSetupPlan(
        steps=(
            NamedRunSetup("proof-horizon", lambda current: calls.append(("proof", current)) or 37),
            NamedRunSetup("other", lambda current: calls.append(("other", current)) or "ok"),
        )
    )

    assert plan.names == ("proof-horizon", "other")
    assert plan.setup_run_state(args) == {
        "proof-horizon": 37,
        "other": "ok",
    }
    assert calls == [("proof", args), ("other", args)]


def test_authority_run_setup_plan_rejects_duplicate_step_names():
    setup = lambda _args: None
    with pytest.raises(ValueError, match="unique"):
        AuthorityRunSetupPlan(
            steps=(
                NamedRunSetup("same", setup),
                NamedRunSetup("same", setup),
            )
        )


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
