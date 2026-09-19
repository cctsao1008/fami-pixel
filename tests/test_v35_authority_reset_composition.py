from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys
from types import SimpleNamespace


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_authority_reset_composition", path)
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


def test_current_authority_reset_plan_freezes_exact_historical_descent_order():
    v35 = _load_v35()
    plan = v35._AUTHORITY_RUN_RESET_PLAN

    assert type(plan).__module__ == "fami_pixel.control.authority_runtime"
    assert plan.names == (
        "v34-handoff-cache",
        "v33-deadline-cache",
        "v32-collect-response-cache",
        "v29-collect-response-cache",
        "v28-authority-plan-memory",
        "v27-progress-response-cache",
        "v27-authority-action-ledger",
        "v26-gap-commitment",
        "v25-live-objective",
    )

    assert v35._V29._COLLECT_RESPONSE_CACHE is v35._V30.v29._COLLECT_RESPONSE_CACHE
    assert type(v35._PROGRESS_CONTROL.cache).__module__ == "fami_pixel.control.progress"
    assert v35._V27.v26 is v35.v26
    assert v35._V24 is v35.v25.v24


def test_current_authority_reset_plan_executes_concrete_dependencies_in_order(monkeypatch):
    v35 = _load_v35()
    calls = []

    class Clearable:
        def __init__(self, name):
            self.name = name

        def clear(self):
            calls.append(self.name)

    handoff = Clearable("handoff")
    deadline = Clearable("deadline")
    collect = Clearable("collect")
    authority_plan = Clearable("authority-plan")
    progress = Clearable("progress")
    ledger = Clearable("ledger")
    live_objective = Clearable("live-objective")

    monkeypatch.setattr(v35.v34, "_install_handoff_cache", lambda: handoff)
    monkeypatch.setattr(v35._V33, "_install_deadline_cache", lambda: deadline)
    monkeypatch.setattr(v35._V29, "_COLLECT_RESPONSE_CACHE", collect)
    monkeypatch.setattr(v35._V28, "_AUTHORITY_PLAN_MEMORY", authority_plan)
    monkeypatch.setattr(v35, "_PROGRESS_CONTROL", SimpleNamespace(cache=progress))
    monkeypatch.setattr(v35._V27, "_AUTHORITY_ACTION_LEDGER", ledger)
    monkeypatch.setattr(v35.v26, "_reset_gap_commitment", lambda: calls.append("gap"))
    monkeypatch.setattr(v35.v25, "_LIVE_OBJECTIVE", live_objective)

    v35._AUTHORITY_RUN_RESET_PLAN.reset_run_state()

    assert calls == [
        "handoff",
        "deadline",
        "collect",
        "collect",
        "authority-plan",
        "progress",
        "ledger",
        "gap",
        "live-objective",
    ]


def test_v35_owns_runtime_resets_through_v25_then_bootstraps_into_stable_live_loop():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    prefix = '_AUTHORITY_RUN_RESET_PLAN.reset_through("v32-collect-response-cache")'
    setup = "_AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)"
    v29_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v29-collect-response-cache")'
    v28_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v28-authority-plan-memory")'
    scope = "with installed_checkpoint_request_enricher(enricher):"
    v27_progress = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-progress-response-cache")'
    v27_ledger = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v27-authority-action-ledger")'
    lineage = "authority_action_recording_layer(_V27._AUTHORITY_ACTION_LEDGER)"
    v26_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v26-gap-commitment")'
    v25_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v25-live-objective")'
    bootstrap = "bootstrap = _V23_BOOTSTRAP_SETUP_PLAN.setup_run_state(args)"
    build = "live_control = _build_live_authority_control()"
    delegate = "return live_control.run(args)"

    for token in (
        prefix,
        setup,
        v29_reset,
        v28_reset,
        scope,
        v27_progress,
        v27_ledger,
        lineage,
        v26_reset,
        v25_reset,
        bootstrap,
        build,
        delegate,
    ):
        assert token in source
    assert (
        source.index(prefix)
        < source.index(setup)
        < source.index(v29_reset)
        < source.index(v28_reset)
        < source.index(scope)
        < source.index(v27_progress)
        < source.index(v27_ledger)
        < source.index(lineage)
        < source.index(v26_reset)
        < source.index(v25_reset)
        < source.index(bootstrap)
        < source.index(build)
        < source.index(delegate)
    )
    assert "return _V17.authority_main(args)" not in source
    assert "return v23.authority_main(args)" not in source
    assert "return v26._BASE_V25_AUTHORITY(args)" not in source
    assert "return v26.authority_main(args)" not in source
    assert "return _V27.authority_main(args)" not in source
    assert "return _V28.authority_main(args)" not in source
    assert "return _V29.authority_main(args)" not in source

    # Historical examples retain standalone behavior for provenance.
    assert "_COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v35._V29.authority_main)
    historical_v28 = inspect.getsource(v35._V28.authority_main)
    assert "_AUTHORITY_PLAN_MEMORY.clear()" in historical_v28
    assert "with installed_checkpoint_request_enricher(enricher):" in historical_v28
    historical_v27 = inspect.getsource(v35._V27.authority_main)
    assert "_PROGRESS_RESPONSE_CACHE.clear()" in historical_v27
    assert "_AUTHORITY_ACTION_LEDGER.clear()" in historical_v27
    historical_v26 = inspect.getsource(v35.v26.authority_main)
    assert "_reset_gap_commitment()" in historical_v26
    assert "return _BASE_V25_AUTHORITY(args)" in historical_v26
    historical_v25 = inspect.getsource(v35.v25.authority_main)
    assert "_LIVE_OBJECTIVE.clear()" in historical_v25
    assert "return _BASE_V24_AUTHORITY(args)" in historical_v25
    historical_v24 = inspect.getsource(v35._V24.authority_main)
    assert "return _BASE_AUTHORITY_MAIN(args)" in historical_v24
    historical_v23 = inspect.getsource(v35.v23.authority_main)
    assert "return v17.authority_main(args)" in historical_v23


def test_v35_preserves_runtime_order_while_bypassing_v29_through_v17(
    monkeypatch,
    tmp_path,
):
    v35 = _load_v35()
    calls = []

    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_RESET_PLAN",
        SimpleNamespace(
            reset_through=lambda name: calls.append(("reset-through", name)),
            reset_named=lambda name: calls.append(("reset-named", name)),
        ),
    )
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: calls.append(("setup", "v30"))),
    )
    monkeypatch.setattr(
        v35,
        "_V23_BOOTSTRAP_SETUP_PLAN",
        SimpleNamespace(
            setup_run_state=lambda _args: calls.append(("setup", "v23"))
            or {"v23-runtime-dir": Path("test-runtime")}
        ),
    )

    def forbidden(_args):
        raise AssertionError("bypassed historical wrapper was invoked")

    monkeypatch.setattr(v35._V29, "authority_main", forbidden)
    monkeypatch.setattr(v35._V28, "authority_main", forbidden)
    monkeypatch.setattr(v35._V27, "authority_main", forbidden)
    monkeypatch.setattr(v35.v26, "authority_main", forbidden)
    monkeypatch.setattr(v35.v25, "authority_main", forbidden)
    monkeypatch.setattr(v35._V24, "authority_main", forbidden)
    monkeypatch.setattr(v35.v23, "authority_main", forbidden)
    monkeypatch.setattr(v35._V17, "authority_main", forbidden)

    class LiveControl:
        def run(self, _args):
            calls.append(("delegate", "stable-live"))
            return 17

    monkeypatch.setattr(v35, "_build_live_authority_control", lambda: LiveControl())

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 17
    assert calls == [
        ("reset-through", "v32-collect-response-cache"),
        ("setup", "v30"),
        ("reset-named", "v29-collect-response-cache"),
        ("reset-named", "v28-authority-plan-memory"),
        ("reset-named", "v27-progress-response-cache"),
        ("reset-named", "v27-authority-action-ledger"),
        ("reset-named", "v26-gap-commitment"),
        ("reset-named", "v25-live-objective"),
        ("setup", "v23"),
        ("delegate", "stable-live"),
    ]
