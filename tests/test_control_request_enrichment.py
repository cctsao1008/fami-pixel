from fami_pixel.control.authority_plan import AuthorityPlanMemory
from fami_pixel.control.request_enrichment import (
    AuthorityContinuationRequestEnricher,
    RequestPayloadEnricherSlot,
)


def _project(schedule, *, start_age: int, frames: int):
    values = []
    flat = []
    for segment in schedule:
        flat.extend([int(segment["buttons"])] * int(segment["frames"]))
    if not flat:
        return []
    for offset in range(frames):
        index = start_age + offset
        values.append(flat[index] if index < len(flat) else flat[-1])
    return [{"buttons": value, "frames": 1} for value in values]


def test_request_enricher_slot_is_identity_by_default_and_copies_payload():
    slot = RequestPayloadEnricherSlot("test")
    source = {"generation": 4, "frame": 100}
    enriched = slot.enrich(source)

    assert enriched == source
    assert enriched is not source


def test_request_enricher_slot_restores_previous_enricher_after_scope():
    slot = RequestPayloadEnricherSlot("test")
    slot.install(lambda payload: {**payload, "outer": True})

    with slot.installed(lambda payload: {**payload, "inner": True}):
        assert slot.enrich({"frame": 1}) == {"frame": 1, "inner": True}

    assert slot.enrich({"frame": 1}) == {"frame": 1, "outer": True}


def test_authority_continuation_enricher_projects_real_schedule_phase():
    memory = AuthorityPlanMemory()
    assert memory.remember(
        {
            "root_frame": 200,
            "candidate": "fm_brake_jump",
            "schedule": [
                {"buttons": 0x42, "frames": 4},
                {"buttons": 0x00, "frames": 2},
                {"buttons": 0x82, "frames": 1},
                {"buttons": 0x83, "frames": 15},
            ],
        }
    )
    enrich = AuthorityContinuationRequestEnricher(
        memory,
        proof_horizon=4,
        projector=_project,
    )

    payload = enrich({"generation": 9, "frame": 208, "checkpoint": "live.mss"})

    assert payload["authority_continuation_candidate"] == "fm_brake_jump"
    assert payload["authority_continuation_root_frame"] == 200
    assert len(payload["authority_continuation_schedule"]) == 4
    assert payload["authority_continuation_schedule"][0]["buttons"] == 0x83


def test_authority_continuation_enricher_leaves_request_plain_without_memory():
    memory = AuthorityPlanMemory()
    enrich = AuthorityContinuationRequestEnricher(
        memory,
        proof_horizon=4,
        projector=_project,
    )

    payload = enrich({"generation": 1, "frame": 100, "checkpoint": "live.mss"})

    assert "authority_continuation_schedule" not in payload
    assert "authority_continuation_candidate" not in payload
    assert "authority_continuation_root_frame" not in payload
