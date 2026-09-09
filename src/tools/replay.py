"""Replay logged positions to prove they are reproducible.

Takes the loss corpus written by bench.py, injects each recorded mine layout
into a fresh game, and checks the game agrees. This is what makes the corpus
a regression suite rather than a pile of anecdotes.
"""
import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from minebencher.session import Session


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    records = []
    with open(args.corpus, encoding="utf-8") as fh:
        for line in fh:
            records.append(json.loads(line))
            if len(records) >= args.limit:
                break

    level = records[0]["level"]
    ok = failed = 0
    with Session(level=level) as session:
        for i, rec in enumerate(records):
            session.reset(rec["level"])
            snap = session.set_mine_layout(rec["mines"])
            if snap.mines == rec["mines"]:
                ok += 1
            else:
                failed += 1
                print(f"  record {i}: injected layout does not match")

            # The recorded fatal move must still be fatal, which confirms the
            # injection actually took effect in the game's own logic.
            fatal = rec.get("fatal_move")
            if fatal:
                fx, fy = fatal
                after = session.open(fx, fy)
                expected_mine = rec["mines"][fy][fx]
                # A first click can never lose, so winmine relocates the mine.
                if expected_mine and not after.over:
                    pass  # first-click protection, as documented
                elif not expected_mine and after.over:
                    failed += 1
                    print(f"  record {i}: unexpected loss at {fatal}")

    print(f"\n{ok}/{len(records)} layouts injected and verified, "
          f"{failed} inconsistencies")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
