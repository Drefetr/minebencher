"""Report an experiment: floor, candidates, ceiling, same N.

Default is the latest experiment. `--history` inspects one identity
across time. `--pooled` aggregates every recorded run, which mixes sessions
and is not the scientific comparison.

    python src/tools/report.py
    python src/tools/report.py --experiment abc123
    python src/tools/report.py --pooled
"""
import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from minebencher.report import BENCHMARKS_DIR, format_report
from minebencher.store import DEFAULT_DB, ResultStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--level", default=None,
                    choices=["beginner", "intermediate", "expert"])
    ap.add_argument("--history", default=None, metavar="NAME_OR_HASH")
    ap.add_argument("--experiment", default=None)
    ap.add_argument("--pooled", action="store_true",
                    help="aggregate every run; mixes sessions, not an experiment")
    ap.add_argument("--save", action="store_true", default=True,
                    help="save report to benchmarks/ directory (default: True)")
    ap.add_argument("--no-save", dest="save", action="store_false",
                    help="do not save report to disk")
    ap.add_argument("--out", type=Path, default=None,
                    help="custom file path to save report output")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"no results store at {args.db}; run src/tools/bench.py first")
        return 1

    with ResultStore(args.db) as store:
        experiment_id = None if args.pooled else (args.experiment or store.latest_experiment())
        text = format_report(
            store,
            level=args.level,
            experiment_id=experiment_id,
            history_key=args.history,
            pooled=args.pooled,
        )

    sys.stdout.write(text)
    sys.stdout.flush()

    if args.out or args.save:
        if args.out:
            out_file = args.out
            out_file.parent.mkdir(parents=True, exist_ok=True)
        elif args.history:
            BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
            out_file = BENCHMARKS_DIR / f"history_{args.history}.txt"
        elif args.pooled:
            BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
            out_file = BENCHMARKS_DIR / "report_cumulative.txt"
        elif experiment_id:
            exp_dir = BENCHMARKS_DIR / experiment_id
            exp_dir.mkdir(parents=True, exist_ok=True)
            out_file = exp_dir / f"report_{experiment_id}.txt"
        else:
            BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
            out_file = BENCHMARKS_DIR / "report_cumulative.txt"

        out_file.write_text(text, encoding="utf-8")
        try:
            rel = out_file.relative_to(_ROOT)
        except ValueError:
            rel = out_file
        print(f"\n[report saved to {rel}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
