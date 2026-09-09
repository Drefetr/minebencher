"""Validate the memory layout against a live winmine.exe.

Runs a set of cross-checks that only hold if the geometry, indexing and cell
encoding are all correct. The neighbour-count check is the decisive one: it
independently confirms the 32-byte stride, the 1-indexed border offset, the
x/y axis order and the meaning of the 0x80 and 0x40 bits at once.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from minebencher import MinesweeperReader, ProcessNotFound
from minebencher import layout as L


def check(snap):
    results = []

    def ok(name, cond, detail=""):
        results.append((cond, name, detail))

    oracle_mines = sum(row.count(True) for row in snap.mines)
    ok("oracle mine count == reported total",
       oracle_mines == snap.mine_total,
       f"{oracle_mines} vs {snap.mine_total}")

    ok("safe_cells == W*H - mines",
       snap.safe_cells == snap.width * snap.height - snap.mine_total,
       f"{snap.safe_cells} vs {snap.width * snap.height - snap.mine_total}")

    opened = sum(1 for _, _, v in snap.cells() if v >= 0)
    ok("opened counter == open cells on board",
       opened == snap.opened, f"{opened} vs {snap.opened}")

    flags = sum(1 for _, _, v in snap.cells() if v == L.View.FLAGGED)
    ok("mines_left == total - flags placed",
       snap.mines_left == snap.mine_total - flags,
       f"{snap.mines_left} vs {snap.mine_total} - {flags}")

    ok("no BORDER sentinel inside the playfield",
       all(v != L.View.BORDER for _, _, v in snap.cells()))

    ok("no unrecognised cell bytes",
       all(v != L.View.UNKNOWN for _, _, v in snap.cells()))

    # The decisive geometry check.
    bad = []
    for x, y, v in snap.cells():
        if v < 0:
            continue
        n = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < snap.width and 0 <= ny < snap.height and snap.mines[ny][nx]:
                    n += 1
        if n != v:
            bad.append((x, y, v, n))
    ok("every open cell's number == adjacent mines in oracle",
       not bad, f"{len(bad)} mismatches, e.g. {bad[:3]}" if bad else
       f"{opened} open cells checked")

    ok("no open cell sits on a mine (while game is live)",
       snap.over or not any(snap.mines[y][x] for x, y, v in snap.cells() if v >= 0))

    return results


def main():
    try:
        reader = MinesweeperReader()
    except ProcessNotFound:
        print("No winmine.exe running. Start it first (tools/launch.py).")
        return 1

    with reader:
        snap = reader.snapshot()
        print(f"pid={reader.pid}  module base=0x{reader.base:08X}"
              f"  slide=0x{reader.slide:X}")
        print(f"board {snap.width}x{snap.height}  mines={snap.mine_total}"
              f"  left={snap.mines_left}  time={snap.elapsed}s"
              f"  opened={snap.opened}/{snap.safe_cells}")
        print(f"flags=0x{snap.flags:02X} (active={snap.active}, over={snap.over})"
              f"  status={snap.status.name}\n")

        print("PLAYER VIEW (what a solver may legitimately use)")
        print(snap.render())
        print("\nORACLE VIEW (ground truth; validation only)")
        print(snap.render(reveal=True))

        print("\nCONSISTENCY CHECKS")
        results = check(snap)
        for good, name, detail in results:
            mark = "PASS" if good else "FAIL"
            print(f"  [{mark}] {name}" + (f"   ({detail})" if detail else ""))
        failed = sum(1 for g, _, _ in results if not g)
        print(f"\n{len(results) - failed}/{len(results)} checks passed")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
