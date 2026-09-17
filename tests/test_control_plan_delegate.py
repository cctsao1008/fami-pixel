import pytest

from fami_pixel.control import PlanDelegateSlot


def _selector(name):
    def select(response_paths, current_frame, freshness, last_applied_generation, live_radar):
        return {
            "name": name,
            "paths": list(response_paths),
            "frame": current_frame,
            "freshness": freshness,
            "generation": last_applied_generation,
            "radar": live_radar,
        }

    return select


def test_plan_delegate_uses_default_selector_until_explicitly_replaced():
    default = _selector("default")
    replacement = _selector("replacement")
    slot = PlanDelegateSlot("lower-objective", default)

    assert slot.is_default
    assert slot.select(["a"], 100, 16, 4, {"mode": "PROGRESS"})["name"] == "default"

    slot.install(replacement)
    assert not slot.is_default
    result = slot.select(["b"], 104, 12, 5, {"mode": "COLLECT"})
    assert result["name"] == "replacement"
    assert result["frame"] == 104
    assert result["radar"] == {"mode": "COLLECT"}


def test_plan_delegate_reset_restores_original_dependency():
    default = _selector("default")
    slot = PlanDelegateSlot("lower-objective", default)
    slot.install(_selector("temporary"))
    slot.reset()

    assert slot.is_default
    assert slot.selector is default


def test_plan_delegate_rejects_invalid_dependencies():
    with pytest.raises(ValueError):
        PlanDelegateSlot("", _selector("default"))
    with pytest.raises(TypeError):
        PlanDelegateSlot("lower-objective", None)  # type: ignore[arg-type]

    slot = PlanDelegateSlot("lower-objective", _selector("default"))
    with pytest.raises(TypeError):
        slot.install(None)  # type: ignore[arg-type]
