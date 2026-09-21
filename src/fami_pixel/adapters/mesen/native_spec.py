"""Exact in-process speculative schedule adapter for the fami-pixel Mesen fork.

The fork-owned speculative instance restores a serializable live decision root
and executes variable-input schedules at the same debugger PPU-period
boundaries used by the authoritative one-frame path.  The adapter deliberately
exposes machine witnesses only; SMB1 reward/death/ranking semantics stay in the
game layer.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Iterable

from .loader import MesenCore, MesenLoadError


NES_INTERNAL_RAM_SIZE = 0x800

NATIVE_SPEC_EXPORTS: tuple[str, ...] = (
    "FamiPixelSpecInitFromLive",
    "FamiPixelSpecCaptureRootFromLive",
    "FamiPixelSpecResetToRoot",
    "FamiPixelSpecRunSchedule",
    "FamiPixelSpecGetNesControllerState",
    "FamiPixelSpecReadNesInternalRam",
    "FamiPixelSpecGetFrameCount",
    "FamiPixelSpecRelease",
)


@dataclass(frozen=True)
class NativeSpecFrameWitness:
    """One exact speculative PPU-period boundary witness."""

    frame_count: int
    controller: int
    ram: bytes


class NativeSpecRunner:
    """Persistent native speculative instance attached to one live ``MesenCore``.

    The underlying Mesen fork owns the speculative ``Emulator`` instance.  This
    Python object binds its ABI, controls serializable root capture/reset, and
    returns coherent RAM/controller/frame witnesses.
    """

    def __init__(self, core: MesenCore) -> None:
        self._core = core
        self._initialized = False
        missing = [name for name in NATIVE_SPEC_EXPORTS if not core.has_export(name)]
        if missing:
            raise MesenLoadError(
                "MesenCore.dll does not contain the exact native speculative schedule ABI: "
                + ", ".join(missing)
            )

        dll = core._dll

        self._init = dll.FamiPixelSpecInitFromLive
        self._init.argtypes = []
        self._init.restype = ctypes.c_int32

        self._capture = dll.FamiPixelSpecCaptureRootFromLive
        self._capture.argtypes = []
        self._capture.restype = ctypes.c_int32

        self._reset = dll.FamiPixelSpecResetToRoot
        self._reset.argtypes = []
        self._reset.restype = ctypes.c_int32

        self._run_schedule = dll.FamiPixelSpecRunSchedule
        self._run_schedule.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        ]
        self._run_schedule.restype = ctypes.c_int32

        self._controller = dll.FamiPixelSpecGetNesControllerState
        self._controller.argtypes = [ctypes.c_uint32]
        self._controller.restype = ctypes.c_int32

        self._read_ram = dll.FamiPixelSpecReadNesInternalRam
        self._read_ram.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
        ]
        self._read_ram.restype = ctypes.c_int32

        self._frame_count = dll.FamiPixelSpecGetFrameCount
        self._frame_count.argtypes = []
        self._frame_count.restype = ctypes.c_uint32

        self._release = dll.FamiPixelSpecRelease
        self._release.argtypes = []
        self._release.restype = None

    @property
    def initialized(self) -> bool:
        return self._initialized

    @staticmethod
    def _check(name: str, status: int, meanings: dict[int, str]) -> None:
        value = int(status)
        if value == 0:
            return
        detail = meanings.get(value, "unknown native status")
        raise MesenLoadError(f"{name} failed ({value}: {detail}).")

    def initialize_from_live(self) -> None:
        """Create the native speculative emulator from the live serializable root."""

        self._check(
            "FamiPixelSpecInitFromLive",
            self._init(),
            {
                1: "live emulator unavailable or root capture failed",
                2: "live console is not NES",
                3: "speculative ROM load failed",
                4: "speculative root deserialize failed",
            },
        )
        self._initialized = True

    def capture_root_from_live(self) -> None:
        """Replace the cached speculative root with the live serializable root.

        Mesen's save-state lock may settle an already paused PPU-cycle boundary
        forward to the next safe CPU instruction boundary before serialization.
        Callers that require strict provenance must therefore treat the state
        *after* this call as the canonical decision root and re-read live
        observations from that root.
        """

        if not self._initialized:
            raise MesenLoadError("initialize_from_live() must be called before root capture")
        self._check(
            "FamiPixelSpecCaptureRootFromLive",
            self._capture(),
            {
                1: "live/spec emulator unavailable or root capture failed",
                2: "live console is not NES",
                3: "live ROM differs from initialized speculative ROM",
                4: "speculative root deserialize failed",
            },
        )

    def reset_to_root(self) -> None:
        """Restore the cached current root before evaluating another candidate."""

        if not self._initialized:
            raise MesenLoadError("initialize_from_live() must be called before root reset")
        self._check(
            "FamiPixelSpecResetToRoot",
            self._reset(),
            {
                1: "speculative runner unavailable",
                4: "speculative root deserialize failed",
            },
        )

    def frame_count(self) -> int:
        if not self._initialized:
            raise MesenLoadError("initialize_from_live() must be called before frame_count()")
        return int(self._frame_count())

    def controller(self, port: int = 0) -> int:
        if not self._initialized:
            raise MesenLoadError("initialize_from_live() must be called before controller()")
        if int(port) not in (0, 1):
            raise ValueError("port must be 0 or 1")
        value = int(self._controller(int(port)))
        if value < 0:
            raise MesenLoadError("speculative NES controller is unavailable")
        return value

    def ram(self) -> bytes:
        """Return the complete speculative 2 KiB NES internal RAM image."""

        if not self._initialized:
            raise MesenLoadError("initialize_from_live() must be called before ram()")
        buffer = (ctypes.c_uint8 * NES_INTERNAL_RAM_SIZE)()
        self._check(
            "FamiPixelSpecReadNesInternalRam",
            self._read_ram(0, buffer, NES_INTERNAL_RAM_SIZE),
            {
                1: "speculative runner unavailable",
                2: "invalid NES internal-RAM range",
                3: "output pointer unavailable",
            },
        )
        return bytes(buffer)

    def run_schedule(
        self,
        buttons: Iterable[int],
        *,
        port: int = 0,
    ) -> tuple[NativeSpecFrameWitness, ...]:
        """Execute one variable-input schedule and return every exact witness.

        The native side runs the schedule continuously.  It captures RAM,
        controller state, and frame count synchronously at each debugger
        PPU-period boundary, so Python never participates in an intermediate
        emulator sleep/wake handshake.
        """

        if not self._initialized:
            raise MesenLoadError("initialize_from_live() must be called before run_schedule()")
        if int(port) not in (0, 1):
            raise ValueError("port must be 0 or 1")

        schedule = tuple(int(value) & 0xFF for value in buttons)
        if not schedule:
            raise ValueError("buttons schedule must not be empty")

        count = len(schedule)
        button_buf = (ctypes.c_uint8 * count)(*schedule)
        ram_buf = (ctypes.c_uint8 * (count * NES_INTERNAL_RAM_SIZE))()
        controller_buf = (ctypes.c_uint8 * count)()
        frame_buf = (ctypes.c_uint32 * count)()

        status = self._run_schedule(
            int(port),
            button_buf,
            count,
            ram_buf,
            len(ram_buf),
            controller_buf,
            len(controller_buf),
            frame_buf,
            len(frame_buf),
        )
        self._check(
            "FamiPixelSpecRunSchedule",
            status,
            {
                1: "speculative runner unavailable",
                2: "speculative console is not NES",
                3: "invalid port/count/buttons",
                4: "NES controller unavailable",
                5: "output pointer/capacity invalid",
                6: "speculative debugger unavailable",
                7: "boundary capture or next-step failure",
                8: "direct speculative frame gate rejected execution",
                9: "expected boundary count was not reached",
            },
        )

        ram = bytes(ram_buf)
        return tuple(
            NativeSpecFrameWitness(
                frame_count=int(frame_buf[index]),
                controller=int(controller_buf[index]),
                ram=ram[
                    index * NES_INTERNAL_RAM_SIZE : (index + 1) * NES_INTERNAL_RAM_SIZE
                ],
            )
            for index in range(count)
        )

    def release(self) -> None:
        """Release the native speculative instance without touching live authority."""

        try:
            self._release()
        finally:
            self._initialized = False

    def __enter__(self) -> "NativeSpecRunner":
        self.initialize_from_live()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
