"""A driveable Minesweeper session: reader + input, synchronised on memory.

Every action posts a message and then polls process memory until the game
state actually changes, so the harness runs as fast as winmine can process
its message queue instead of at the speed of guessed sleeps.
"""
from __future__ import annotations

import ctypes as C
import subprocess
import time
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
        if pid is None and not find_pids() and launch:
            subprocess.Popen([str(EXE_PATH)], cwd=str(EXE_PATH.parent))
            for _ in range(50):
                if find_pids():
                    break
                time.sleep(0.1)
        self.reader = MinesweeperReader(pid, writable=True)
        self._lock = _ExclusiveLock(self.reader.pid)
        self.window = GameWindow(sync_timeout_ms=int(timeout * 1000))
        self._level = level

        self.suppress_highscore_dialog()
        self.disable_marks()
        self.window.set_level(self.reader, level)
        self.window.calibrate(self.reader)

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
        self.reader.close()
        self._lock.release()

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

    # --- position injection ----------------------------------------------
    def set_mine_layout(self, mines: list[list[bool]]) -> Snapshot:
        """Overwrite the mine bits on a freshly started game.

        This is the one case where writing memory is clearly better than
        clicking: it lets us reproduce a specific board exactly, which turns
        the logged loss corpus into a deterministic regression suite. It is
        safe because it only edits *state* and leaves every state transition
        to the game's own code -- the counters stay consistent so long as the
        mine count is unchanged, which is checked below.

        Note that winmine still relocates a mine if the very first click
        lands on one, so a replay is faithful only while the recorded opening
        move is reproduced too.
        """
        snap = self.state()
        if snap.opened:
            raise RuntimeError("set_mine_layout requires a fresh game")
        want = sum(row.count(True) for row in mines)
        if want != snap.mine_total:
            raise ValueError(f"layout has {want} mines but the current game "
                             f"expects {snap.mine_total}")
        if len(mines) != snap.height or len(mines[0]) != snap.width:
            raise ValueError("layout dimensions do not match the board")

        board = bytearray(snap.raw)
        for y in range(snap.height):
            for x in range(snap.width):
                i = (y + 1) * L.ROW_STRIDE + (x + 1)
                board[i] = (board[i] | L.MINE_BIT) if mines[y][x] \
                    else (board[i] & ~L.MINE_BIT)
        self.reader.write_bytes(L.ADDR_BOARD, bytes(board))
        return self.state()
