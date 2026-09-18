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

    # V32 and V29 intentionally clear the same historical cache. The explicit
    # composition preserves both calls instead of silently deduplicating them.
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


def test_v35_owns_v29_reset_after_v30_setup_and_enters_at_v28():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    prefix = '_AUTHORITY_RUN_RESET_PLAN.reset_through("v32-collect-response-cache")'
    setup = "_AUTHORITY_RUN_SETUP_PLAN.setup_run_state(args)"
    v29_reset = '_AUTHORITY_RUN_RESET_PLAN.reset_named("v29-collect-response-cache")'
    delegate = "return _V28.authority_main(args)"

    assert prefix in source
    assert setup in source
    assert v29_reset in source
    assert delegate in source
    assert source.index(prefix) < source.index(setup) < source.index(v29_reset) < source.index(delegate)
    assert "return _V29.authority_main(args)" not in source
    assert "return _V30.authority_main(args)" not in source

    # Historical examples retain standalone behavior for provenance.
    assert "v29._COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v35._V32.authority_main)
    assert "v28.COLLECT_PROOF_HORIZON = int(proof_horizon)" in inspect.getsource(v35._V30.authority_main)
    assert "_COLLECT_RESPONSE_CACHE.clear()" in inspect.getsource(v35._V29.authority_main)


def test_v35_preserves_v32_setup_v29_runtime_order_while_bypassing_v29(
    monkeypatch,
    tmp_path,
):
    v35 = _load_v35()
    calls = []

    class Clearable:
        def __init__(self, name):
            self.name = name

        def clear(self):
            calls.append(self.name)

    collect = Clearable("collect")
    monkeypatch.setattr(v35.v34, "_install_handoff_cache", lambda: Clearable("handoff"))
    monkeypatch.setattr(v35._V33, "_install_deadline_cache", lambda: Clearable("deadline"))
    monkeypatch.setattr(v35._V29, "_COLLECT_RESPONSE_CACHE", collect)
    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: calls.append("v30-setup")),
    )

    def forbidden(_args):
        raise AssertionError("bypassed historical wrapper was invoked")

    monkeypatch.setattr(v35._V32, "authority_main", forbidden)
    monkeypatch.setattr(v35._V30, "authority_main", forbidden)
    monkeypatch.setattr(v35._V29, "authority_main", forbidden)

    def delegated(_args):
        calls.append("v28")
        return 23

    monkeypatch.setattr(v35._V28, "authority_main", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 23
    assert calls == ["handoff", "deadline", "collect", "v30-setup", "collect", "v28"]
