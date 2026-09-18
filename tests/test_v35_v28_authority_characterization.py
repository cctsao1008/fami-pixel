from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import inspect
import sys
from types import SimpleNamespace

from fami_pixel.control import enrich_checkpoint_request


def _load_v35():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v35.py"
        spec = spec_from_file_location("planner_v35_v28_authority_characterization", path)
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


def test_v28_historical_authority_responsibility_is_memory_reset_enrichment_log_and_delegate():
    v35 = _load_v35()
    source = inspect.getsource(v35._V28.authority_main)

    assert "_AUTHORITY_PLAN_MEMORY.clear()" in source
    assert "AuthorityContinuationRequestEnricher(" in source
    assert "proof_horizon=COLLECT_PROOF_HORIZON" in source
    assert "projector=schedule_window" in source
    assert '"Planner V28: delay-compensated COLLECT enabled | "' in source
    assert "with installed_checkpoint_request_enricher(enricher):" in source
    assert "return v27.authority_main(args)" in source


def test_current_v35_extracts_v28_runtime_and_enters_v27_directly():
    v35 = _load_v35()
    source = inspect.getsource(v35.authority_main)

    assert '_AUTHORITY_RUN_RESET_PLAN.reset_named("v28-authority-plan-memory")' in source
    assert "enricher = _v28_checkpoint_request_enricher()" in source
    assert "with installed_checkpoint_request_enricher(enricher):" in source
    assert "return _V27.authority_main(args)" in source
    assert "return _V28.authority_main(args)" not in source

    helper = inspect.getsource(v35._v28_checkpoint_request_enricher)
    assert "AuthorityContinuationRequestEnricher(" in helper
    assert "_V28._AUTHORITY_PLAN_MEMORY" in helper
    assert "proof_horizon=int(_V28.COLLECT_PROOF_HORIZON)" in helper
    assert "projector=_V28.schedule_window" in helper


def test_v35_v28_enricher_scope_is_bounded_and_uses_same_authority_plan_memory(
    monkeypatch,
    tmp_path,
):
    v35 = _load_v35()
    memory = v35._V28._AUTHORITY_PLAN_MEMORY

    assert memory.remember(
        {
            "root_frame": 1,
            "candidate": "stale-before-run",
            "schedule": [{"buttons": 0x00, "frames": 24}],
        }
    )
    assert memory.snapshot is not None

    class ResetPlan:
        def reset_through(self, _name):
            pass

        def reset_named(self, name):
            if name == "v28-authority-plan-memory":
                v35._reset_v28_authority_plan_memory()

    monkeypatch.setattr(v35, "_AUTHORITY_RUN_RESET_PLAN", ResetPlan())
    monkeypatch.setattr(
        v35,
        "_AUTHORITY_RUN_SETUP_PLAN",
        SimpleNamespace(setup_run_state=lambda _args: None),
    )
    monkeypatch.setattr(v35.v11, "_log", lambda *_args, **_kwargs: None)

    observed = {}

    def delegated(_args):
        # Current-run V28 memory must be empty before V27 starts.
        assert memory.snapshot is None
        assert memory.remember(
            {
                "root_frame": 100,
                "candidate": "authority-plan",
                "schedule": [{"buttons": 0x80, "frames": 24}],
            }
        )
        observed.update(enrich_checkpoint_request({"frame": 100, "generation": 7}))
        return 28

    monkeypatch.setattr(v35._V27, "authority_main", delegated)

    args = SimpleNamespace(
        step_timeout=1.0,
        checkpoint_dir=tmp_path / "checkpoints" / "live.mss",
    )
    assert v35.authority_main(args) == 28

    assert observed["authority_continuation_schedule"] == [{"buttons": 0x80, "frames": 24}]
    assert observed["authority_continuation_candidate"] == "authority-plan"
    assert observed["authority_continuation_root_frame"] == 100

    # The stable enricher scope must restore the prior identity hook after the run.
    assert enrich_checkpoint_request({"frame": 100, "generation": 8}) == {
        "frame": 100,
        "generation": 8,
    }
