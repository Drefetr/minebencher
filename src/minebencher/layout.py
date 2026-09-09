"""Memory layout of WINMINE.EXE 5.1.2600.0 (XP SP0).

Every address here was derived by disassembling the shipped binary (see
tools/static_map.py and tools/disasm.py), not copied from a trainer table.
The binary has relocations stripped and no DYNAMICBASE flag, so it always
loads at its preferred base of 0x01000000 -- but the reader still resolves
the module base at runtime rather than assuming it.

Key evidence:
  0x01002ED5  InitBoard   fills 0x360 bytes with 0x0F, then writes 0x10
              sentinels along row 0, row H+1, col 0 and col W+1, using
              a 32-byte row stride.
  0x01003008  UncoverCell rejects (v & 0x40) [already open], (v & 0x1F)==0x10
              [border] and (v & 0x1F)==0x0E [flagged], then ORs in 0x40.
  0x0100367A  StartGame   seeds mines by OR-ing 0x80, and initialises the
              counters below.
  0x0100347C  EndGame     writes 2 (lost) / 3 (won) to 0x01005160.
"""
from enum import IntEnum

IMAGE_BASE = 0x01000000

# --- .data section: read in a single pass for a coherent snapshot ----------
DATA_BASE = 0x01005000
DATA_SIZE = 0x00000B98

# --- Globals (absolute VAs at the preferred image base) -------------------
ADDR_FLAGS = 0x01005000      # dword bitfield, see GameFlag
ADDR_STATUS = 0x01005160     # dword: 0 = in play, 2 = lost, 3 = won
ADDR_MINES_LEFT = 0x01005194  # dword: mine counter shown in the UI
ADDR_DIFFICULTY = 0x010056A0  # word: 0 beginner, 1 intermediate, 2 expert, 3 custom
ADDR_MINE_TOTAL = 0x01005330  # dword: total mines this game
ADDR_WIDTH = 0x01005334      # dword: columns (x, the fast axis)
ADDR_HEIGHT = 0x01005338     # dword: rows (y, the 32-byte-strided axis)
ADDR_BOARD = 0x01005340      # byte[27][32], 1-indexed, 0x10 border
ADDR_TIME = 0x0100579C       # dword: elapsed seconds, capped at 999
ADDR_SAFE_CELLS = 0x010057A0  # dword: W*H - mines (the win target)
ADDR_OPENED = 0x010057A4     # dword: cells uncovered so far

# Preferences. EndGame (0x0100347C) pops the "new best time" dialog when
# elapsed < best_time[level] and level != 3, which would block automation;
# zeroing these three dwords makes that comparison always fail.
ADDR_BEST_TIMES = 0x010056CC  # dword[3], indexed by difficulty level
ADDR_MARKS = 0x010056BC      # dword: non-zero enables '?' marks

ROW_STRIDE = 32
MAX_ROWS = 27

# Hard limits from the Custom dialog; used purely as sanity checks.
MAX_WIDTH, MAX_HEIGHT = 30, 24


class GameFlag(IntEnum):
    ACTIVE = 0x01   # a game is running
    PAUSED = 0x02   # window deactivated; timer suspended
    OVER = 0x10     # game finished (set by EndGame)


class Status(IntEnum):
    PLAYING = 0
    LOST = 2
    WON = 3


# --- Cell byte encoding ---------------------------------------------------
MINE_BIT = 0x80   # a mine is buried here (present even while covered!)
OPEN_BIT = 0x40   # cell has been uncovered; low nibble = adjacent mine count
VALUE_MASK = 0x1F  # low bits used for the covered-cell state

COVERED = 0x0F
FLAGGED = 0x0E
QUESTION = 0x0D
BORDER = 0x10

# Post-mortem markers written once the game ends. RevealBoard (0x01002F80)
# rewrites every covered cell as `(cell & 0xE0) | marker`, with marker 0x0A on
# a loss and 0x0E on a win; non-mine cells that were flagged become 0x0B. The
# mine actually stepped on is stamped with 0x4C by SetCell (0x01002EAB),
# yielding 0xCC.
REVEALED_MINE = 0x0A   # 0x8A: mine exposed after a loss
MISFLAG = 0x0B         # 0x0B: flag placed where there was no mine
EXPLODED = 0x0C        # 0xCC: the mine that ended the game


class View(IntEnum):
    """What the *player* can legitimately see. Values 0-8 are open counts."""
    COVERED = -1
    FLAGGED = -2
    QUESTION = -3
    BORDER = -4
    UNKNOWN = -5
    REVEALED_MINE = -6
    MISFLAG = -7
    EXPLODED = -8
