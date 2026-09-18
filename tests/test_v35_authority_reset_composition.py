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
    )

    assert v35._V29._COLLECT_RESPONSE_CACHE is v35._V30.v29._COLLECT_RESPONSE_CACHE
    assert v35._V27.v26 is v35.v26


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

    monkeypatch.setattr(v35.v34, "_install_handoff_cache", lambda: handoff)
    monkeypatch.setattr(v35._V33, "_install_deadline_cache", lambda: deadline)
    monkeypatch.setattr(v35._V29, "_COLLECT_RESPONSE_CACHE", collect)
    monkeypatch.setattr(v35._V28, "_AUTHORITY_PLAN_MEMORY", authority_plan)
    monkeypatch.setattr(v35._V27, "_PROGRESS_RESPONSE_CACHE", progress)
    monkeypatch.setattr(v35._V27, "_AUTHORITY_ACTION_LEDGER", ledger)
    monkeypatch.setattr(v35.v26, "_reset_gap_commitment", lambda: calls.append("gap"))

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
    ]


def test_v35_owns_v27_resets_inside_v28_enrichment_and_enters_v26():
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
    delegate = "return v26.authority_main(args)"

    for token in (
        prefix,
        setup,
        v29_reset,
        v28_reset,
        scope,
        v27_progress,
        v27_ledger,
        lineage,
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
        < source.index(delegate)
    )
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


def test_v35_preserves_runtime_order_while_bypassing_v29_v28_v27(
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

    def forbidden(_args):
        raise AssertionError("bypassed historical wrapper was invoked")

    monkeypatch.setattr(v35._V29, "authority_main", forbidden)
    monkeypatch.setattr(v35._V28, "authority_main", forbidden)
    monkeypatch.setattr(v35._V27, "authority_main", forbidden)

    def delegated(_args):
        calls.append(("delegate", "v26"))
        return 23

    monkeypatch.setattr(v35.v26, "authority_main", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 23
    assert calls == [
        ("reset-through", "v32-collect-response-cache"),
        ("setup", "v30"),
        ("reset-named", "v29-collect-response-cache"),
        ("reset-named", "v28-authority-plan-memory"),
        ("reset-named", "v27-progress-response-cache"),
        ("reset-named", "v27-authority-action-ledger"),
        ("delegate", "v26"),
    ]
