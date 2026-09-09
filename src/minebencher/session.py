"""A driveable Minesweeper session: reader + input, synchronised on memory.

Every action posts a message and then polls process memory until the game
state actually changes, so the harness runs as fast as winmine can process
its message queue instead of at the speed of guessed sleeps.
"""
from __future__ import annotations

import ctypes as C
import subprocess
from pathlib import Path
from typing import Optional

from . import layout as L
from .input import GameWindow
from .input import WindowBusy as Stalled
from .reader import MinesweeperReader, ProcessNotFound, Snapshot, find_pids

_ROOT = Path(__file__).resolve().parent.parent.parent if Path(__file__).resolve().parent.parent.name == "src" else Path(__file__).resolve().parent.parent
EXE_PATH = _ROOT / "bin" / "WINMINE.EXE"
if not EXE_PATH.exists():
    EXE_PATH = _ROOT / "binaries" / "WINMINE.EXE"
if not EXE_PATH.exists():
    EXE_PATH = _ROOT / "WINMINE.EXE"

kernel32 = C.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = C.c_void_p
kernel32.ReleaseMutex.argtypes = [C.c_void_p]
kernel32.CloseHandle.argtypes = [C.c_void_p]
ERROR_ALREADY_EXISTS = 183


class SessionInUse(RuntimeError):
    """Another Session is already driving this winmine process."""


class _ExclusiveLock:
    """A named mutex keyed on the target pid.

    Two Sessions sharing one winmine interleave their clicks on the same
    board and produce results that look plausible but are nonsense -- an
    agent that cannot lose reporting a 0% win rate, for instance. The mutex
    is named rather than in-process because the usual way to hit this is two
    separate command-line runs.
    """

    def __init__(self, pid: int):
        self.name = f"Local\\winmine-harness-{pid}"
        self.handle = kernel32.CreateMutexW(None, True, self.name)
        if not self.handle:
            raise OSError(C.get_last_error(), "CreateMutexW failed")
        if C.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(C.c_void_p(self.handle))
            self.handle = None
            raise SessionInUse(
                f"winmine pid {pid} is already being driven by another "
                "session; run benchmarks sequentially or against separate "
                "instances")

    def release(self) -> None:
        if self.handle:
            kernel32.ReleaseMutex(C.c_void_p(self.handle))
            kernel32.CloseHandle(C.c_void_p(self.handle))
            self.handle = None


class Session:
    def __init__(self, level: str = "beginner", pid: Optional[int] = None,
                 launch: bool = True, timeout: float = 2.0):
        self._process: Optional[subprocess.Popen] = None
        self.reader = None
        self._lock = None
        try:
            if pid is None:
                if launch:
                    if not EXE_PATH.exists():
                        raise FileNotFoundError(
                            f"WINMINE.EXE not found; expected {EXE_PATH}")
                    self._process = subprocess.Popen(
                        [str(EXE_PATH)], cwd=str(EXE_PATH.parent))
                    pid = self._process.pid
                else:
                    pids = find_pids()
                    if not pids:
                        raise ProcessNotFound("no running winmine.exe found")
                    pid = pids[0]

            self.reader = MinesweeperReader(pid, writable=True)
            self._lock = _ExclusiveLock(self.reader.pid)
            self.window = GameWindow(
                pid=self.reader.pid, sync_timeout_ms=int(timeout * 1000))
            self._level = level

            self.suppress_highscore_dialog()
            self.disable_marks()
            self.window.set_level(self.reader, level)
            self.window.calibrate(self.reader)
        except Exception:
            self.close()
            raise

    # --- setup -----------------------------------------------------------
    def suppress_highscore_dialog(self) -> None:
        """Zero the three best times.

        Winning faster than the record pops a modal name-entry dialog, which
        would stall the harness indefinitely. EndGame only shows it when
        elapsed < best_time[level], so a best time of zero can never trigger.
        """
        for i in range(3):
            self.reader.write_u32(L.ADDR_BEST_TIMES + 4 * i, 0)

    def disable_marks(self) -> None:
        """Turn off '?' marks so right-click is a clean flag toggle."""
        if self.reader.read_u32(L.ADDR_MARKS):
            self.reader.write_u32(L.ADDR_MARKS, 0)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            self._process = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- state -----------------------------------------------------------
    def state(self) -> Snapshot:
        return self.reader.snapshot()

    # --- actions ---------------------------------------------------------
    # Each click syncs on the window's message queue, so the snapshot taken
    # immediately afterwards is guaranteed to include its effect.
    def reset(self, level: Optional[str] = None) -> Snapshot:
        if level and level != self._level:
            self.window.set_level(self.reader, level)
            self._level = level
        return self.window.new_game(self.reader)

    def open(self, x: int, y: int) -> Snapshot:
        self.window.left_click(x, y)
        return self.state()

    def flag(self, x: int, y: int) -> Snapshot:
        self.window.right_click(x, y)
        return self.state()
