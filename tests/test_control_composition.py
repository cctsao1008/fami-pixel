from pathlib import Path

import fami_pixel.control.composition as composition
from fami_pixel.control import (
    CollectProgressControl,
    EagerCollectControl,
    EagerCollectDecision,
    ProgressControl,
)
from fami_pixel.planning import ProgressSelection


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


def test_progress_control_binds_stable_intake_cache_lineage_and_selector(monkeypatch):
    paths = [Path("worker-0.json")]
    payload = {"planner_mode": "progress-search", "generation": 7}
    groups = {(7, 120): [{"candidate": "run", "root_frame": 120}]}
    ledger = object()
    read_paths = []
    cache_calls = []
    helper_calls = []
    captured = {}
    sentinel = ProgressSelection(
        plan={"candidate": "run"},
        meta={"forward_model_status": "selected-lineage-cohort"},
    )

    class FakeCache:
        def ingest(self, responses, **kwargs):
            cache_calls.append((responses, kwargs))

        def groups(self):
            return groups

    def reader(path):
        read_paths.append(path)
        return payload

    def fake_anchors(proofs, *, required_anchors):
        helper_calls.append(("anchors", proofs, required_anchors))
        return {"run"}

    def fake_lineage(proofs, *, ledger, current_frame, commit_frames):
        helper_calls.append(
            ("lineage", proofs, ledger, current_frame, commit_frames)
        )
        return proofs, {}

    def fake_select(seen_groups, **kwargs):
        captured["groups"] = seen_groups
        captured.update(kwargs)
        assert kwargs["evaluated_anchors"]([{"candidate": "run"}]) == {"run"}
        assert kwargs["lineage_validator"](
            [{"candidate": "run"}], current_frame=124
        ) == ([{"candidate": "run"}], {})
        return sentinel

    monkeypatch.setattr(composition, "evaluated_anchor_set", fake_anchors)
    monkeypatch.setattr(composition, "lineage_valid_proofs", fake_lineage)
    monkeypatch.setattr(composition, "select_progress_proof", fake_select)

    control = ProgressControl(
        response_reader=reader,
        cache=FakeCache(),
        required_anchors=frozenset({"run"}),
        ledger=ledger,
        commit_frames=4,
        live_horizon_frames=64,
    )

    decision = control.decide(
        paths,
        current_frame=124,
        freshness=16,
        last_applied_generation=5,
        live_radar={"camera_x": 10},
    )

    assert decision is sentinel
    assert read_paths == paths
    assert cache_calls == [
        (
            [payload],
            {
                "current_frame": 124,
                "freshness": 16,
                "last_applied_generation": 5,
                "live_horizon_frames": 64,
            },
        )
    ]
    assert captured["groups"] is groups
    assert captured["current_frame"] == 124
    assert captured["required_anchors"] == frozenset({"run"})
    assert captured["live_radar"] == {"camera_x": 10}
    assert helper_calls == [
        ("anchors", [{"candidate": "run"}], frozenset({"run"})),
        ("lineage", [{"candidate": "run"}], ledger, 124, 4),
    ]


def test_collect_progress_control_routes_no_target_to_stable_progress():
    radar = {"camera_x": 123}
    progress_calls = []
    progress_result = ProgressSelection(
        plan={"candidate": "progress-right"},
        meta={"forward_model_status": "selected-lineage-cohort"},
    )

    class FailEager:
        def decide(self, *args, **kwargs):
            raise AssertionError("COLLECT control must not run without a target")

    class FakeProgress:
        def decide(self, response_paths, **kwargs):
            progress_calls.append((response_paths, kwargs))
            return progress_result

    control = CollectProgressControl(
        target_selector=lambda seen: None,
        progress=FakeProgress(),
        eager_collect=FailEager(),
    )

    assert control.target_type(radar) is None
    decision = control.decide(
        [Path("worker-0.json")],
        current_frame=80,
        freshness=12,
        last_applied_generation=7,
        target_type=None,
        live_radar=radar,
    )

    assert decision.plan == {"candidate": "progress-right"}
    assert decision.meta == progress_result.meta
    assert decision.target_type is None
    assert progress_calls == [
        (
            [Path("worker-0.json")],
            {
                "current_frame": 80,
                "freshness": 12,
                "last_applied_generation": 7,
                "live_radar": radar,
            },
        )
    ]


def test_collect_progress_control_routes_target_to_stable_eager_collect():
    radar = {"collect_target_type": "mushroom"}
    eager_calls = []
    eager_result = EagerCollectDecision(
        plan={"candidate": "collect_delay4_right"},
        meta={"forward_model_status": "selected-eager-handoff-collect-proof"},
    )

    class FakeEager:
        def decide(self, response_paths, **kwargs):
            eager_calls.append((response_paths, kwargs))
            return eager_result

    class FailProgress:
        def decide(self, *args, **kwargs):
            raise AssertionError("PROGRESS must not run while a COLLECT target exists")

    control = CollectProgressControl(
        target_selector=lambda seen: seen.get("collect_target_type"),
        progress=FailProgress(),
        eager_collect=FakeEager(),
    )

    target = control.target_type(radar)
    assert target == "mushroom"
    decision = control.decide(
        [Path("worker-0.json")],
        current_frame=90,
        freshness=16,
        last_applied_generation=8,
        target_type=target,
        live_radar=radar,
    )

    assert decision.plan == eager_result.plan
    assert decision.meta == eager_result.meta
    assert decision.target_type == "mushroom"
    assert eager_calls == [
        (
            [Path("worker-0.json")],
            {
                "current_frame": 90,
                "freshness": 16,
                "last_applied_generation": 8,
                "target_type": "mushroom",
                "live_radar": radar,
            },
        )
    ]
