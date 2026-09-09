"""Drive winmine.exe by posting mouse messages to its window.

Clicks are addressed in *cell* coordinates. Two things make this reliable:

1. Geometry is measured, not assumed. `calibrate()` derives the grid origin
   by right-clicking a probe cell and asking process memory which cell
   actually got marked.
2. Input is *sent*, not posted. A cross-thread SendMessage runs the target's
   window procedure and only returns once it has been handled, so the memory
   read that follows is guaranteed to see its effect.

Two approaches that look reasonable and are not:

  - PostMessage plus a sleep, or plus a poll for a state predicate. Absolute
    predicates such as "no cells are open" are frequently already true before
    the command is even dequeued, so the wait returns early and the queued
    command lands later, clobbering the following step.
  - PostMessage plus SendMessageTimeout(WM_NULL) as a queue barrier. Windows
    dispatches inter-thread *sent* messages ahead of *posted* ones, so the
    WM_NULL overtakes the very clicks it was meant to flush.
"""
from __future__ import annotations

import ctypes as C
import time
from ctypes import wintypes as W
from typing import Optional

from . import layout as L

user32 = C.WinDLL("user32", use_last_error=True)

WM_NULL = 0x0000
WM_COMMAND = 0x0111
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002
VK_F2 = 0x71
SMTO_ABORTIFHUNG = 0x0002

# Menu command ids from the binary's resources.
ID_NEW = 510
ID_BEGINNER, ID_INTERMEDIATE, ID_EXPERT, ID_CUSTOM = 521, 522, 523, 524
LEVELS = {"beginner": ID_BEGINNER, "intermediate": ID_INTERMEDIATE,
          "expert": ID_EXPERT}

# Classic XP metrics; treated as a starting guess and then verified.
CELL = 16
ORIGIN_X, ORIGIN_Y = 12, 55

user32.EnumWindows.restype = W.BOOL
user32.GetWindowThreadProcessId.restype = W.DWORD
user32.PostMessageW.argtypes = [W.HWND, C.c_uint, W.WPARAM, W.LPARAM]
user32.SendMessageTimeoutW.argtypes = [
    W.HWND, C.c_uint, W.WPARAM, W.LPARAM, C.c_uint, C.c_uint,
    C.POINTER(C.c_size_t),
]

WNDENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, W.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]


class WindowBusy(RuntimeError):
    """The window did not process messages in time.

    Usually means a modal dialog is up, since its own message loop starves
    the main window.
    """


def _lparam(px: int, py: int) -> int:
    return ((py & 0xFFFF) << 16) | (px & 0xFFFF)


def _find_window_for_pid(pid: int) -> Optional[int]:
    """Find the Minesweeper top-level window owned by exactly `pid`."""
    found: list[int] = []

    @WNDENUMPROC
    def visit(hwnd, _lparam):
        owner = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, C.byref(owner))
        if owner.value != pid:
            return True

        class_name = C.create_unicode_buffer(256)
        title = C.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        user32.GetWindowTextW(hwnd, title, len(title))
        if class_name.value == "Minesweeper" or title.value == "Minesweeper":
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


class GameWindow:
    def __init__(self, hwnd: Optional[int] = None, pid: Optional[int] = None,
                 sync_timeout_ms: int = 3000):
        if hwnd is None:
            if pid is None:
                raise ValueError("GameWindow requires a target pid or hwnd")
            for _ in range(50):
                hwnd = _find_window_for_pid(pid)
                if hwnd:
                    break
                time.sleep(0.1)
        if not hwnd:
            raise RuntimeError(f"Minesweeper window for pid {pid} not found")
        self.hwnd = hwnd
        self.origin = (ORIGIN_X, ORIGIN_Y)
        self.cell = CELL
        self.sync_timeout_ms = sync_timeout_ms

    def send(self, msg: int, wparam: int = 0, lparam: int = 0) -> int:
        """Send a message and block until the window procedure has run it."""
        result = C.c_size_t(0)
        ok = user32.SendMessageTimeoutW(
            self.hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG,
            self.sync_timeout_ms, C.byref(result))
        if not ok:
            raise WindowBusy(
                f"Minesweeper did not handle message 0x{msg:04X}; "
                "a modal dialog is probably open")
        return result.value

    def sync(self) -> None:
        self.send(WM_NULL)

    def pixel_of(self, cx: int, cy: int) -> tuple[int, int]:
        ox, oy = self.origin
        return ox + cx * self.cell + self.cell // 2, oy + cy * self.cell + self.cell // 2

    def _click(self, down: int, up: int, mk: int, cx: int, cy: int) -> None:
        lp = _lparam(*self.pixel_of(cx, cy))
        self.send(down, mk, lp)
        self.send(up, 0, lp)

    def left_click(self, cx: int, cy: int) -> None:
        self._click(WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, cx, cy)

    def right_click(self, cx: int, cy: int) -> None:
        self._click(WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, cx, cy)

    def new_game(self, reader=None):
        """Start a fresh game.

        WM_COMMAND is used rather than a posted F2 keystroke: the accelerator
        does not reliably reach the window when it is not focused, which
        silently leaves the previous board (and its flags) in place.
        """
        self.send(WM_COMMAND, ID_NEW, 0)
        if reader is None:
            return None
        snap = reader.snapshot()
        if snap.opened or snap.over or snap.mines_left != snap.mine_total:
            raise RuntimeError(f"new game did not take effect: opened="
                               f"{snap.opened} over={snap.over} "
                               f"left={snap.mines_left}/{snap.mine_total}")
        return snap

    def set_level(self, reader, level: str):
        self.send(WM_COMMAND, LEVELS[level.lower()], 0)
        return reader.snapshot() if reader else None

    def calibrate(self, reader, probe: tuple[int, int] | None = None) -> tuple[int, int]:
        """Right-click a cell, see which cell memory says got marked, and
        correct the grid origin by the observed delta."""
        snap = self.new_game(reader)
        cx, cy = probe or (snap.width // 2, snap.height // 2)

        self.right_click(cx, cy)
        after = reader.snapshot()
        marked = [(x, y) for x, y, v in after.cells()
                  if v in (L.View.FLAGGED, L.View.QUESTION)]
        if len(marked) != 1:
            raise RuntimeError(
                f"calibration failed: expected exactly 1 marked cell, got "
                f"{marked}. Is the window visible and un-minimised?")

        mx, my = marked[0]
        ox, oy = self.origin
        self.origin = (ox + (cx - mx) * self.cell, oy + (cy - my) * self.cell)
        self.new_game(reader)  # clear the probe mark, and confirm it cleared
        return self.origin
