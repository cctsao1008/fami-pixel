import pytest

from fami_pixel.adapters.mesen import MesenLoadError
from fami_pixel.adapters.mesen.native_spec import (
    NES_INTERNAL_RAM_SIZE,
    NativeSpecRunner,
)


class _FakeFn:
    def __init__(self, impl):
        self.impl = impl
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.impl(*args)


class _FakeDll:
    def __init__(self):
        self.calls = []
        self.frame = 100
        self.controller = 0x82
        self.ram = bytearray(NES_INTERNAL_RAM_SIZE)
        self.ram[0x10] = 0x44

        self.FamiPixelSpecInitFromLive = _FakeFn(self._init)
        self.FamiPixelSpecCaptureRootFromLive = _FakeFn(self._capture)
        self.FamiPixelSpecResetToRoot = _FakeFn(self._reset)
        self.FamiPixelSpecRunSchedule = _FakeFn(self._run_schedule)
        self.FamiPixelSpecGetNesControllerState = _FakeFn(self._controller)
        self.FamiPixelSpecReadNesInternalRam = _FakeFn(self._read_ram)
        self.FamiPixelSpecGetFrameCount = _FakeFn(self._frame_count)
        self.FamiPixelSpecRelease = _FakeFn(self._release)

    def _init(self):
        self.calls.append("init")
        self.frame = 100
        return 0

    def _capture(self):
        self.calls.append("capture")
        self.frame = 200
        self.ram[0x10] = 0x55
        return 0

    def _reset(self):
        self.calls.append("reset")
        return 0

    def _controller(self, port):
        return self.controller if int(port) == 0 else 0

    def _read_ram(self, address, output, length):
        start = int(address)
        count = int(length)
        for index in range(count):
            output[index] = self.ram[start + index]
        return 0

    def _frame_count(self):
        return self.frame

    def _release(self):
        self.calls.append("release")

    def _run_schedule(
        self,
        port,
        buttons,
        frame_count,
        ram_output,
        ram_capacity,
        controller_output,
        controller_capacity,
        frame_output,
        frame_capacity,
    ):
        count = int(frame_count)
        assert int(port) == 0
        assert int(ram_capacity) == count * NES_INTERNAL_RAM_SIZE
        assert int(controller_capacity) == count
        assert int(frame_capacity) == count
        self.calls.append(("schedule", tuple(int(buttons[i]) for i in range(count))))
        for index in range(count):
            controller_output[index] = buttons[index]
            frame_output[index] = self.frame + index + 1
            base = index * NES_INTERNAL_RAM_SIZE
            ram_output[base + 0] = index + 1
            ram_output[base + 0x06] = 0x40 + index
            ram_output[base + NES_INTERNAL_RAM_SIZE - 1] = 0xA0 + index
        self.frame += count
        return 0


class _FakeCore:
    def __init__(self, dll=None):
        self._dll = dll or _FakeDll()

    def has_export(self, name):
        return hasattr(self._dll, name)


def test_native_spec_runner_returns_exact_per_boundary_witnesses():
    core = _FakeCore()
    runner = NativeSpecRunner(core)

    runner.initialize_from_live()
    assert runner.frame_count() == 100
    assert runner.controller(0) == 0x82
    assert len(runner.ram()) == NES_INTERNAL_RAM_SIZE
    assert runner.ram()[0x10] == 0x44

    runner.reset_to_root()
    witnesses = runner.run_schedule((0x82, 0x83, 0x00, 0x42))

    assert [w.frame_count for w in witnesses] == [101, 102, 103, 104]
    assert [w.controller for w in witnesses] == [0x82, 0x83, 0x00, 0x42]
    assert all(len(w.ram) == NES_INTERNAL_RAM_SIZE for w in witnesses)
    assert [w.ram[0] for w in witnesses] == [1, 2, 3, 4]
    assert [w.ram[0x06] for w in witnesses] == [0x40, 0x41, 0x42, 0x43]
    assert [w.ram[-1] for w in witnesses] == [0xA0, 0xA1, 0xA2, 0xA3]

    runner.capture_root_from_live()
    assert runner.frame_count() == 200
    assert runner.ram()[0x10] == 0x55
    runner.release()
    assert core._dll.calls[-1] == "release"


def test_native_spec_runner_rejects_missing_exact_schedule_abi():
    core = _FakeCore()
    del core._dll.FamiPixelSpecRunSchedule

    with pytest.raises(MesenLoadError, match="FamiPixelSpecRunSchedule"):
        NativeSpecRunner(core)


def test_native_spec_runner_surfaces_native_schedule_failure():
    core = _FakeCore()
    core._dll.FamiPixelSpecRunSchedule = _FakeFn(lambda *args: 9)
    runner = NativeSpecRunner(core)
    runner.initialize_from_live()

    with pytest.raises(MesenLoadError, match="expected boundary count was not reached"):
        runner.run_schedule((0x82,))
