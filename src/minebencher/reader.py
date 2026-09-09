"""Read live Minesweeper board state out of a running WINMINE.EXE.

The whole .data section is pulled in one ReadProcessMemory call so the board
and its counters come from a single coherent instant.

IMPORTANT: process memory contains the mine locations for *covered* cells.
`Snapshot.view` is the masked, honest player view; the ground truth is kept
separately in `Snapshot.mines` and should only ever be used for validating
a solver, never as a solver input.
"""
from __future__ import annotations

import ctypes as C
import struct
from ctypes import wintypes as W
from dataclasses import dataclass
from typing import Iterator, Optional

from . import layout as L

kernel32 = C.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = C.c_void_p(-1).value


class PROCESSENTRY32(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", C.POINTER(C.c_ulong)), ("th32ModuleID", W.DWORD),
        ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
        ("pcPriClassBase", C.c_long), ("dwFlags", W.DWORD),
        ("szExeFile", C.c_char * 260),
    ]


class MODULEENTRY32(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD), ("th32ModuleID", W.DWORD), ("th32ProcessID", W.DWORD),
        ("GlblcntUsage", W.DWORD), ("ProccntUsage", W.DWORD),
        ("modBaseAddr", C.c_void_p), ("modBaseSize", W.DWORD),
        ("hModule", C.c_void_p), ("szModule", C.c_char * 256),
        ("szExePath", C.c_char * 260),
    ]


kernel32.CreateToolhelp32Snapshot.restype = C.c_void_p
kernel32.OpenProcess.restype = C.c_void_p
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.CloseHandle.argtypes = [C.c_void_p]
kernel32.ReadProcessMemory.argtypes = [
    C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)
]
kernel32.WriteProcessMemory.argtypes = [
    C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)
]


class ProcessNotFound(RuntimeError):
    pass


def find_pids(name: str = "winmine.exe") -> list[int]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(C.get_last_error(), "CreateToolhelp32Snapshot failed")
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = C.sizeof(PROCESSENTRY32)
        found = []
        ok = kernel32.Process32First(C.c_void_p(snap), C.byref(entry))
        while ok:
            if entry.szExeFile.decode(errors="replace").lower() == name.lower():
                found.append(entry.th32ProcessID)
            ok = kernel32.Process32Next(C.c_void_p(snap), C.byref(entry))
        return found
    finally:
        kernel32.CloseHandle(C.c_void_p(snap))


def module_base(pid: int, name: str = "winmine.exe") -> Optional[int]:
    snap = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snap == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = MODULEENTRY32()
        entry.dwSize = C.sizeof(MODULEENTRY32)
        ok = kernel32.Module32First(C.c_void_p(snap), C.byref(entry))
        while ok:
            if entry.szModule.decode(errors="replace").lower() == name.lower():
                return entry.modBaseAddr or 0
            ok = kernel32.Module32Next(C.c_void_p(snap), C.byref(entry))
        return None
    finally:
        kernel32.CloseHandle(C.c_void_p(snap))


@dataclass(frozen=True)
class Snapshot:
    width: int
    height: int
    mine_total: int
    mines_left: int      # UI counter: total minus flags placed
    elapsed: int
    opened: int
    safe_cells: int
    flags: int
    status: L.Status
    view: list[list[int]]    # [y][x], 0-8 open count or a negative L.View
    mines: list[list[bool]]  # ORACLE: true mine positions. Validation only.
    raw: bytes               # untouched board bytes, for debugging

    @property
    def active(self) -> bool:
        return bool(self.flags & L.GameFlag.ACTIVE)

    @property
    def over(self) -> bool:
        return bool(self.flags & L.GameFlag.OVER)

    def cells(self) -> Iterator[tuple[int, int, int]]:
        for y in range(self.height):
            for x in range(self.width):
                yield x, y, self.view[y][x]

    def render(self, reveal: bool = False) -> str:
        glyph = {
            L.View.COVERED: ".", L.View.FLAGGED: "F",
            L.View.QUESTION: "?", L.View.BORDER: "#", L.View.UNKNOWN: "!",
            L.View.REVEALED_MINE: "*", L.View.MISFLAG: "X",
            L.View.EXPLODED: "@",
        }
        head = "    " + "".join(f"{x % 10}" for x in range(self.width))
        out = [head, "   +" + "-" * self.width + "+"]
        for y in range(self.height):
            row = []
            for x in range(self.width):
                v = self.view[y][x]
                if reveal and self.mines[y][x] and v in (L.View.COVERED, L.View.QUESTION):
                    ch = "*"
                elif reveal and self.mines[y][x] and v == L.View.FLAGGED:
                    ch = "F"
                elif reveal and not self.mines[y][x] and v == L.View.FLAGGED:
                    ch = "X"          # misflag
                elif v >= 0:
                    ch = " " if v == 0 else str(v)
                else:
                    ch = glyph.get(v, "!")
                row.append(ch)
            out.append(f"{y:2d} |" + "".join(row) + "|")
        out.append("   +" + "-" * self.width + "+")
        return "\n".join(out)


class MinesweeperReader:
    """Attaches to a running winmine.exe and snapshots its board state."""

    def __init__(self, pid: Optional[int] = None, writable: bool = False):
        if pid is None:
            pids = find_pids()
            if not pids:
                raise ProcessNotFound("no running winmine.exe found")
            pid = pids[0]
        self.pid = pid
        self.writable = writable
        self.base = module_base(pid) or L.IMAGE_BASE
        self.slide = self.base - L.IMAGE_BASE
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = kernel32.OpenProcess(access, False, pid)
        if not self.handle:
            raise OSError(C.get_last_error(), f"OpenProcess({pid}) failed")

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(C.c_void_p(self.handle))
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, addr: int, size: int) -> bytes:
        buf = (C.c_ubyte * size)()
        got = C.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            C.c_void_p(self.handle), C.c_void_p(addr + self.slide),
            C.byref(buf), size, C.byref(got)
        )
        if not ok or got.value != size:
            raise OSError(C.get_last_error(),
                          f"ReadProcessMemory(0x{addr:08X}, {size}) failed")
        return bytes(buf)

    def read_u32(self, addr: int) -> int:
        return struct.unpack("<I", self._read(addr, 4))[0]

    def write_bytes(self, addr: int, data: bytes) -> None:
        if not self.writable:
            raise PermissionError("reader was not opened with writable=True")
        buf = (C.c_ubyte * len(data)).from_buffer_copy(data)
        wrote = C.c_size_t(0)
        ok = kernel32.WriteProcessMemory(
            C.c_void_p(self.handle), C.c_void_p(addr + self.slide),
            C.byref(buf), len(data), C.byref(wrote)
        )
        if not ok or wrote.value != len(data):
            raise OSError(C.get_last_error(),
                          f"WriteProcessMemory(0x{addr:08X}) failed")

    def write_u32(self, addr: int, value: int) -> None:
        self.write_bytes(addr, struct.pack("<I", value))

    def snapshot(self) -> Snapshot:
        blob = self._read(L.DATA_BASE, L.DATA_SIZE)

        def u32(addr: int) -> int:
            return struct.unpack_from("<I", blob, addr - L.DATA_BASE)[0]

        def u16(addr: int) -> int:
            return struct.unpack_from("<H", blob, addr - L.DATA_BASE)[0]

        width, height = u32(L.ADDR_WIDTH), u32(L.ADDR_HEIGHT)
        if not (1 <= width <= L.MAX_WIDTH and 1 <= height <= L.MAX_HEIGHT):
            raise ValueError(f"implausible board dimensions {width}x{height}; "
                             "layout may not match this binary")

        board_off = L.ADDR_BOARD - L.DATA_BASE
        raw = blob[board_off:board_off + L.ROW_STRIDE * L.MAX_ROWS]

        view = [[L.View.UNKNOWN] * width for _ in range(height)]
        mines = [[False] * width for _ in range(height)]
        for y in range(height):
            for x in range(width):
                # The array is 1-indexed with a sentinel border, so the
                # player-visible cell (x, y) lives at [(y+1)*32 + (x+1)].
                b = raw[(y + 1) * L.ROW_STRIDE + (x + 1)]
                mines[y][x] = bool(b & L.MINE_BIT)
                low = b & L.VALUE_MASK
                if (b & L.OPEN_BIT) and low <= 8:
                    view[y][x] = low
                elif low == L.COVERED:
                    view[y][x] = L.View.COVERED
                elif low == L.FLAGGED:
                    view[y][x] = L.View.FLAGGED
                elif low == L.QUESTION:
                    view[y][x] = L.View.QUESTION
                elif low == L.BORDER:
                    view[y][x] = L.View.BORDER
                elif low == L.REVEALED_MINE:
                    view[y][x] = L.View.REVEALED_MINE
                elif low == L.MISFLAG:
                    view[y][x] = L.View.MISFLAG
                elif low == L.EXPLODED:
                    view[y][x] = L.View.EXPLODED
                else:
                    view[y][x] = L.View.UNKNOWN

        status_raw = u32(L.ADDR_STATUS)
        try:
            status = L.Status(status_raw)
        except ValueError:
            status = L.Status.PLAYING

        return Snapshot(
            width=width, height=height,
            mine_total=u32(L.ADDR_MINE_TOTAL),
            mines_left=struct.unpack_from(
                "<i", blob, L.ADDR_MINES_LEFT - L.DATA_BASE)[0],
            elapsed=u32(L.ADDR_TIME),
            opened=u32(L.ADDR_OPENED),
            safe_cells=u32(L.ADDR_SAFE_CELLS),
            flags=u32(L.ADDR_FLAGS),
            status=status,
            view=view, mines=mines, raw=raw,
        )
