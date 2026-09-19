"""Stable COLLECT target detection from live radar payloads."""

from __future__ import annotations


COLLECT_TARGET_TYPES = frozenset({"mushroom", "fire_flower", "star", "one_up"})


def collect_target_from_radar(radar: dict) -> str | None:
    """Return the explicit/sticky COLLECT target type recognized by current policy.

    Preserve V25 precedence exactly: ``collect_target_type`` wins over the
    instantaneous ``nearest_reward_type`` field, and unrecognized values do not
    create a COLLECT objective.
    """

    value = radar.get("collect_target_type") or radar.get("nearest_reward_type")
    if value in COLLECT_TARGET_TYPES:
        return str(value)
    return None
