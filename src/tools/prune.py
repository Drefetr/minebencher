"""Inspect and prune identities in the results store.

Identity is the source hash. Pass a fingerprint to delete that player.

    python tools/prune.py
    python tools/prune.py --fingerprint abc123 --dry-run
    python tools/prune.py --fingerprint abc123
"""
import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from minebencher.store import DEFAULT_DB, ResultStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--label", default=None,
                    help="filter the listing to a display name")
    ap.add_argument("--fingerprint", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with ResultStore(args.db) as store:
        if not args.fingerprint:
            print(f"  {'hash':<12}  {'label':<16} {'runs':>5} "
                  f"{'games':>6}  first seen")
            print("  " + "-" * 70)
            for r in store.fingerprints(args.label):
                label = f"{r['agent_id']}@{r['version']}"
                print(f"  {r['fingerprint']:<12}  {label:<16} {r['runs']:>5} "
                      f"{r['games']:>6}  {r['first_seen']}")
            print("\n  pass --fingerprint to delete an identity")
            return 0

        rows = [r for r in store.fingerprints()
                if r["fingerprint"] == args.fingerprint]
        if not rows:
            print(f"no runs for [{args.fingerprint}]")
            return 1
        games = sum(r["games"] for r in rows)
        runs = sum(r["runs"] for r in rows)
        labels = ", ".join(sorted({f"{r['agent_id']}@{r['version']}" for r in rows}))
        first = min(r["first_seen"] for r in rows)
        last = max(r["last_seen"] for r in rows)
        print(f"  [{args.fingerprint}] {labels}: "
              f"{runs} runs, {games} games ({first} .. {last})")
        if args.dry_run:
            print("  dry run; nothing deleted")
            return 0
        deleted_runs, deleted_games = store.delete_runs(args.fingerprint)
        print(f"  deleted {deleted_runs} runs and {deleted_games} games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
