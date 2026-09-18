from fami_pixel.control.authority_plan import AuthorityPlanMemory


def _project(schedule, *, start_age: int, frames: int):
    remaining = int(frames)
    age = int(start_age)
    out = []
    cursor = 0
    for segment in schedule:
        seg_frames = int(segment["frames"])
        seg_start = cursor
        seg_end = cursor + seg_frames
        cursor = seg_end
        if age >= seg_end:
            continue
        offset = max(0, age - seg_start)
        take = min(seg_frames - offset, remaining)
        if take > 0:
            out.append({"buttons": int(segment["buttons"]), "frames": int(take)})
            remaining -= take
        age = max(age, seg_end)
        if remaining <= 0:
            break
    return out


def test_authority_plan_memory_ignores_invalid_plans_and_clears():
    memory = AuthorityPlanMemory()
    assert memory.snapshot is None
    assert memory.remember(None) is False
    assert memory.remember({"root_frame": 10, "schedule": []}) is False
    assert memory.remember({"root_frame": "bad", "schedule": [{"buttons": 1, "frames": 4}]}) is False
    assert memory.snapshot is None

    assert memory.remember(
        {
            "root_frame": 100,
            "candidate": "fm_run",
            "schedule": [{"buttons": 0x82, "frames": 8}],
        }
    ) is True
    assert memory.snapshot is not None
    memory.clear()
    assert memory.snapshot is None


def test_authority_plan_memory_copies_selected_schedule():
    memory = AuthorityPlanMemory()
    schedule = [{"buttons": 0x82, "frames": 8}]
    assert memory.remember(
        {"root_frame": 100, "candidate": "fm_run", "schedule": schedule}
    )

    schedule[0]["buttons"] = 0
    snapshot = memory.snapshot
    assert snapshot == {
        "root_frame": 100,
        "candidate": "fm_run",
        "schedule": [{"buttons": 0x82, "frames": 8}],
    }

    snapshot["schedule"][0]["buttons"] = 0
    assert memory.snapshot["schedule"][0]["buttons"] == 0x82


def test_authority_plan_memory_projects_from_real_schedule_phase():
    memory = AuthorityPlanMemory()
    memory.remember(
        {
            "root_frame": 200,
            "candidate": "fm_brake_jump",
            "schedule": [
                {"buttons": 0x42, "frames": 4},
                {"buttons": 0x00, "frames": 2},
                {"buttons": 0x82, "frames": 1},
                {"buttons": 0x83, "frames": 15},
                {"buttons": 0x82, "frames": 1},
            ],
        }
    )

    assert memory.continuation(199, frames=24, projector=_project) == []
    continuation = memory.continuation(208, frames=24, projector=_project)
    assert continuation
    assert continuation[0]["buttons"] == 0x83
    assert continuation[0]["frames"] == 14
