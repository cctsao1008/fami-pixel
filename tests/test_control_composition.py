from pathlib import Path

import fami_pixel.control.composition as composition
from fami_pixel.control import EagerCollectControl, EagerCollectDecision


def test_eager_collect_control_binds_current_scan_dependencies(monkeypatch, tmp_path):
    first = tmp_path / "worker-0.json"
    second = tmp_path / "worker-1.json"
    payloads = {
        first: {"worker": 0, "generation": 7},
        second: {"worker": 1, "generation": 7},
    }
    read_paths = []

    def reader(path: Path):
        read_paths.append(path)
        return payloads.get(path)

    cache = object()
    active_calls = []
    horizon_calls = []
    proof_selector = object()
    ledger = object()
    captured = {}
    sentinel = EagerCollectDecision(
        plan={"candidate": "collect_delay4_right"},
        meta={"forward_model_status": "selected-eager-handoff-collect-proof"},
    )

    def fake_select(responses, **kwargs):
        captured["responses"] = responses
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(composition, "select_eager_collect_decision", fake_select)

    control = EagerCollectControl(
        response_reader=reader,
        cache_provider=lambda: cache,
        handoff_frames=(4, 8, 12),
        active_workers=lambda count: active_calls.append(count) or {0, 1},
        proof_selector=proof_selector,
        ledger=ledger,
        proof_horizon_frames=lambda freshness: horizon_calls.append(freshness) or 16,
        commit_frames=4,
    )

    decision = control.decide(
        [first, second],
        current_frame=120,
        freshness=6,
        last_applied_generation=5,
        target_type="star",
        live_radar={"collect_target_type": "star"},
    )

    assert decision is sentinel
    assert read_paths == [first, second]
    assert active_calls == [2]
    assert horizon_calls == [6]
    assert captured["responses"] == [payloads[first], payloads[second]]
    assert captured["cache"] is cache
    assert captured["handoff_frames"] == (4, 8, 12)
    assert captured["current_frame"] == 120
    assert captured["last_applied_generation"] == 5
    assert captured["target_type"] == "star"
    assert captured["retention_frames"] == 16
    assert captured["active_workers"] == {0, 1}
    assert captured["proof_selector"] is proof_selector
    assert captured["ledger"] is ledger
    assert captured["commit_frames"] == 4


def test_eager_collect_control_never_reduces_requested_freshness(monkeypatch, tmp_path):
    captured = {}
    sentinel = EagerCollectDecision(plan=None, meta={"forward_model_status": "waiting"})

    def fake_select(responses, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(composition, "select_eager_collect_decision", fake_select)

    control = EagerCollectControl(
        response_reader=lambda _path: None,
        cache_provider=lambda: object(),
        handoff_frames=(4, 8, 12),
        active_workers=lambda _count: set(),
        proof_selector=object(),
        ledger=object(),
        proof_horizon_frames=lambda _freshness: 4,
        commit_frames=4,
    )

    decision = control.decide(
        [tmp_path / "missing.json"],
        current_frame=10,
        freshness=12,
        last_applied_generation=-1,
        target_type="mushroom",
        live_radar={},
    )

    assert decision is sentinel
    assert captured["retention_frames"] == 12
