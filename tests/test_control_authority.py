from pathlib import Path
from types import SimpleNamespace

from fami_pixel.control import LiveAuthorityControl
from fami_pixel.games.smb1 import GameEventType


class _Worker:
    def __init__(self):
        self.terminated = 0
        self.waited = 0
        self.killed = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout):
        assert timeout == 0.5
        self.waited += 1

    def kill(self):
        self.killed += 1


class _Core:
    def __init__(self, load_ok=True):
        self.load_ok = bool(load_ok)
        self.frame = 10
        self.home = None
        self.debugger = False

    def initialize_headless(self, home):
        self.home = home

    def load_rom(self, _rom):
        return self.load_ok

    def initialize_debugger(self):
        self.debugger = True

    def frame_count(self):
        return self.frame


class _Radar:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def to_payload(self):
        return dict(self.payload)


def _args(tmp_path, *, max_frames=1):
    return SimpleNamespace(
        checkpoint_dir=tmp_path / "ipc",
        shadow_workers=1,
        dll=tmp_path / "mesen.dll",
        home=tmp_path / "home",
        rom=tmp_path / "game.nes",
        web_ui=False,
        web_port=8765,
        control_quantum=1,
        plan_freshness=16,
        max_frames=max_frames,
        step_timeout=1.0,
    )


def _control(*, core, worker, events=None, published=None, terminals=None, controller_calls=None):
    published = published if published is not None else []
    terminals = terminals if terminals is not None else []
    controller_calls = controller_calls if controller_calls is not None else []

    def observe_state(observed_core, _state):
        return SimpleNamespace(
            native_frame_id=observed_core.frame_count(),
            mario_x_abs=100 + observed_core.frame_count(),
            mario_y=48,
        )

    def step_core(observed_core, _timeout):
        observed_core.frame += 1

    return LiveAuthorityControl(
        prepare_run=lambda _args: Path("model.json").resolve(),
        create_recorder=lambda: object(),
        spawn_workers=lambda _args, _request, _responses: [worker],
        core_factory=lambda _dll: core,
        configure_controller=lambda _core, _port: None,
        enter_world=lambda _core, _timeout: object(),
        observe_state=observe_state,
        read_state=lambda _core: object(),
        derive_events=lambda _previous, _current: list(events or []),
        set_controller_state=lambda observed_core, port, buttons: controller_calls.append(
            (observed_core.frame_count(), int(port), int(buttons))
        ),
        step_core=step_core,
        read_radar=lambda _core, **_kwargs: _Radar(),
        radar_reason=lambda _radar: None,
        select_plan=lambda _paths, _frame, _freshness, _last_generation, _radar: None,
        looks_grounded=lambda _current: False,
        emergency_jump_plan=lambda _frame, _radar: {},
        schedule_buttons=lambda _schedule, _age, **_kwargs: 0x80,
        schedule_label=lambda candidate: candidate,
        save_checkpoint=lambda observed_core, _path: (observed_core.frame_count(), 111, 0),
        publish_json=lambda path, payload: published.append((path, dict(payload))) or True,
        append_timeline=lambda _recorder, _payload: None,
        persist_terminal=lambda _recorder, **kwargs: terminals.append(kwargs["terminal"]),
        viewer_factory=lambda **_kwargs: None,
        format_radar_strip=lambda *_args, **_kwargs: "",
        log=lambda _message: None,
        bootstrap_schedule=[{"buttons": 0x80, "frames": 4}],
        jump_names={"jump"},
        radar_lookahead_px=160,
        enemy_trigger_px=56,
        gap_trigger_px=56,
        obstacle_trigger_px=48,
        risk_cutoff=lambda: 0.5,
    )


def test_live_authority_returns_load_failure_and_terminates_spawned_workers(tmp_path):
    worker = _Worker()
    core = _Core(load_ok=False)
    control = _control(core=core, worker=worker)

    assert control.run(_args(tmp_path)) == 2
    assert worker.terminated == 1
    assert worker.waited == 0
    assert core.debugger is False


def test_live_authority_publishes_current_checkpoint_without_waiting_for_plan(tmp_path):
    worker = _Worker()
    core = _Core(load_ok=True)
    published = []
    terminals = []
    controller_calls = []
    control = _control(
        core=core,
        worker=worker,
        published=published,
        terminals=terminals,
        controller_calls=controller_calls,
    )

    assert control.run(_args(tmp_path, max_frames=1)) == 7

    assert len(published) == 1
    request_path, payload = published[0]
    assert request_path == (tmp_path / "ipc" / "request.json").resolve()
    assert payload["generation"] == 1
    assert payload["frame"] == 11
    assert payload["x"] == 111
    assert payload["engine"] == 0
    assert payload["radar"] == {}
    assert terminals == ["frame_limit"]

    # First call applies live authority; final call neutralizes the controller.
    assert controller_calls == [(10, 0, 0x80), (11, 0, 0x00)]
    assert worker.terminated == 1
    assert worker.waited == 1
    assert worker.killed == 0


def test_live_authority_terminal_event_preempts_planner_request(tmp_path):
    worker = _Worker()
    core = _Core(load_ok=True)
    published = []
    terminals = []
    event = SimpleNamespace(kind=GameEventType.DIED)
    control = _control(
        core=core,
        worker=worker,
        events=[event],
        published=published,
        terminals=terminals,
    )

    assert control.run(_args(tmp_path, max_frames=1)) == 6
    assert published == []
    assert terminals == ["death"]
    assert worker.terminated == 1
    assert worker.waited == 1
