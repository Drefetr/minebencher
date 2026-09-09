"""Run an experiment: floor, candidates, ceiling, same N and levels.

The white-noise agent and the oracle are required controls. They run in
the same session as every candidate so the comparison is not across
different days or different sample sizes. `--only` still keeps both
baselines; it only restricts which *candidates* join them.

Each run is archived in benchmarks/<experiment_id>/ with its isolated
results_<experiment_id>.db and report_<experiment_id>.txt, while results are
also unioned into benchmarks/results_cumulative.db and benchmarks/report_cumulative.txt.

    python src/tools/bench.py
    python src/tools/bench.py --games 200 --all-levels
    python src/tools/bench.py --only deductive --level expert
"""
import argparse
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from minebencher.agent import describe
from minebencher.harness import run_and_record
from minebencher.registry import (AGENTS_DIR, IncompleteBaselines, discover,
                                  instantiate, roster)
from minebencher.report import BENCHMARKS_DIR, format_report
from minebencher.session import Session
from minebencher.store import DEFAULT_DB, ResultStore

LEVELS = ["beginner", "intermediate", "expert"]


def _candidates(found, only: str | None):
    extras = [a for a in found.agents if a.baseline is None]
    if not only:
        return extras
    hits = [a for a in extras
            if a.fingerprint.startswith(only) or a.agent_id == only]
    if not hits:
        known = ", ".join(a.fingerprint for a in extras) or "none"
        raise SystemExit(f"--only {only!r} matched no candidate; hashes: {known}")
    return hits


def main():
    found = discover()
    for path, err in found.errors.items():
        print(f"warning: could not load {path}: {err}", file=sys.stderr)
    for path, why in found.skipped.items():
        print(f"note: skipped {path}: {why}", file=sys.stderr)

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="hash prefix or name of candidate; floor and ceiling still run")
    ap.add_argument("--level", default="beginner", choices=LEVELS)
    ap.add_argument("--all-levels", action="store_true")
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help="cumulative database path (default: benchmarks/results_cumulative.db)")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--log-losses", type=Path, default=None)
    ap.add_argument("--progress-every", type=int, default=0)
    args = ap.parse_args()

    try:
        agents = roster(found, extra=_candidates(found, args.only))
    except IncompleteBaselines as exc:
        print(f"cannot run: {exc}", file=sys.stderr)
        print(f"agents directory: {AGENTS_DIR}", file=sys.stderr)
        return 1

    levels = LEVELS if args.all_levels else [args.level]
    experiment_id = uuid.uuid4().hex[:12]

    exp_dir = None
    exp_db_path = None
    exp_store = None
    cum_store = None
    stores = []

    if not args.no_record:
        exp_dir = BENCHMARKS_DIR / experiment_id
        exp_dir.mkdir(parents=True, exist_ok=True)
        exp_db_path = exp_dir / f"results_{experiment_id}.db"
        exp_store = ResultStore(exp_db_path)
        stores.append(exp_store)

        cum_db_path = args.db
        if cum_db_path.resolve() != exp_db_path.resolve():
            cum_store = ResultStore(cum_db_path)
            stores.append(cum_store)

    try:
        with Session(level=levels[0]) as session:
            print(f"experiment {experiment_id}  pid={session.reader.pid}  "
                  f"{len(agents)} agent(s) (floor + "
                  f"{len(agents) - 2} candidate(s) + ceiling), "
                  f"{len(levels)} level(s), N={args.games}\n")
            for reg in agents:
                agent = instantiate(reg, args.seed)
                info = describe(agent)
                role = (info.baseline or "candidate").upper()
                print(f"--- {role}  {info.fingerprint}  {info.label} ---")
                for level in levels:
                    stats, _ = run_and_record(
                        session, agent, args.games, level, store=stores,
                        seed=args.seed, experiment_id=experiment_id,
                        log_losses=args.log_losses,
                        progress_every=args.progress_every)
                    print(stats.table())
                    print()
    finally:
        for s in stores:
            s.close()

    if exp_store and exp_dir and exp_db_path:
        # Generate per-experiment report
        with ResultStore(exp_db_path) as run_store:
            exp_report_text = format_report(run_store, experiment_id=experiment_id)
        exp_report_file = exp_dir / f"report_{experiment_id}.txt"
        exp_report_file.write_text(exp_report_text, encoding="utf-8")

        # Update cumulative report in benchmarks/
        cum_report_file = BENCHMARKS_DIR / "report_cumulative.txt"
        if cum_store:
            with ResultStore(args.db) as pool_store:
                cum_report_text = format_report(pool_store, pooled=True)
            cum_report_file.write_text(cum_report_text, encoding="utf-8")

        try:
            rel_exp_dir = exp_dir.relative_to(_ROOT)
            rel_exp_db = exp_db_path.relative_to(_ROOT)
            rel_exp_report = exp_report_file.relative_to(_ROOT)
        except ValueError:
            rel_exp_dir = exp_dir
            rel_exp_db = exp_db_path
            rel_exp_report = exp_report_file

        print(f"[benchmark {experiment_id} saved]")
        print(f"  run dir:           {rel_exp_dir}")
        print(f"  run database:      {rel_exp_db}")
        print(f"  run report:        {rel_exp_report}")
        if cum_store:
            try:
                rel_cum_db = args.db.relative_to(_ROOT)
                rel_cum_report = cum_report_file.relative_to(_ROOT)
            except ValueError:
                rel_cum_db = args.db
                rel_cum_report = cum_report_file
            print(f"  cumulative db:     {rel_cum_db}")
            print(f"  cumulative report: {rel_cum_report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
