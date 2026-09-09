"""Reporting and leaderboard generation for benchmark runs.

Manages formatted tables with 95% Wilson confidence intervals across
individual experiments and cumulative historical runs.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from .store import DEFAULT_DB, ResultStore, wilson

_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    if Path(__file__).resolve().parent.parent.name == "src"
    else Path(__file__).resolve().parent.parent
)
BENCHMARKS_DIR = _ROOT / "benchmarks"

LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "expert": 2}


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def _role_key(e: dict) -> tuple[int, float]:
    b = e["baseline"] or ""
    rate = e["wins"] / e["games"] if e["games"] else 0.0
    if b == "ceiling":
        return (0, 0.0)
    if b == "floor":
        return (2, 0.0)
    return (1, -rate)


def format_leaderboard(
    store: ResultStore,
    level: Optional[str] = None,
    experiment_id: Optional[str] = None,
) -> str:
    rows = store.leaderboard(level, include_privileged=True, experiment_id=experiment_id)
    if not rows:
        return "  no runs recorded yet\n"

    by_level: dict[str, list] = {}
    for r in rows:
        by_level.setdefault(r["level"], []).append(r)

    out = io.StringIO()
    for lvl in sorted(by_level, key=lambda l: LEVEL_ORDER.get(l, 99)):
        entries = sorted(by_level[lvl], key=_role_key)
        ceiling = next((e for e in entries if e["baseline"] == "ceiling"), None)
        floor = next((e for e in entries if e["baseline"] == "floor"), None)

        print(f"\n  {lvl.upper()}", file=out)
        print(
            f"    {'':1} {'hash':<12}  {'label':<16} {'games':>6} {'win%':>7} "
            f"{'95% CI':>15} {'progress':>9} {'vs floor':>9} {'vs ceil':>8}",
            file=out,
        )
        print("    " + "-" * 102, file=out)
        for e in entries:
            lo, hi = wilson(e["wins"], e["games"])
            rate = e["wins"] / e["games"] if e["games"] else 0.0
            role = e["baseline"] or "candidate"
            mark = {"ceiling": "*", "floor": ".", "candidate": " "}.get(role, " ")
            vs_floor = vs_ceil = "-"
            if role == "ceiling":
                vs_ceil = "ceiling"
            elif role == "floor":
                vs_floor = "floor"
            else:
                if floor and floor["games"]:
                    vs_floor = f"{(rate - floor['wins'] / floor['games']) * 100:6.1f}pp"
                if ceiling and ceiling["games"]:
                    vs_ceil = f"{(ceiling['wins'] / ceiling['games'] - rate) * 100:5.1f}pp"
            label = f"{e['agent_id']}@{e['version']}"
            print(
                f"  {mark} {e['fingerprint']:<12}  {label:<16} "
                f"{e['games']:>6} {pct(rate):>7} "
                f"[{pct(lo)},{pct(hi)}] {pct(e['mean_progress']):>9} "
                f"{vs_floor:>9} {vs_ceil:>8}",
                file=out,
            )

        for e in entries:
            who = f"{e['agent_id']}@{e['version']} [{e['fingerprint']}]"
            if e["unsound_deaths"]:
                print(
                    f"      UNSOUND: {who} died {e['unsound_deaths']} times "
                    f"on moves it called certain -- its deduction is buggy",
                    file=out,
                )
            if (e["illegal"] or e["stuck"] or e["timed_out"] or
                    e["stalled"]):
                print(
                    f"      note: {who} had {e['illegal']} illegal moves, "
                    f"{e['stuck']} stuck, {e['timed_out']} timed out, "
                    f"{e['stalled']} stalled",
                    file=out,
                )

    print(
        "\n  * ceiling (oracle)   . floor (white noise)   candidates unmarked",
        file=out,
    )
    return out.getvalue()


def format_history(store: ResultStore, key: str, level: Optional[str] = None) -> str:
    rows = store.history(key, level)
    if not rows:
        return f"  no runs for {key}\n"
    out = io.StringIO()
    print(f"\n  HISTORY: {key}", file=out)
    print(
        f"    {'when (UTC)':<17} {'hash':<12}  {'label':<16} {'level':<13} "
        f"{'games':>6} {'win%':>7} {'progress':>9}",
        file=out,
    )
    print("    " + "-" * 90, file=out)
    last_hash = None
    for r in rows:
        rate = r["wins"] / r["games"] if r["games"] else 0.0
        when = r["started_at"].replace("+00:00", "").replace("T", " ")[:16]
        label = f"{r['agent_id']}@{r['version']}"
        if last_hash is not None and r["fingerprint"] != last_hash:
            print("    " + "-" * 90, file=out)
        last_hash = r["fingerprint"]
        print(
            f"    {when:<17} {r['fingerprint']:<12}  {label:<16} "
            f"{r['level']:<13} {r['games']:>6} {pct(rate):>7} "
            f"{pct(r['mean_progress']):>9}",
            file=out,
        )
    return out.getvalue()


def format_report(
    store: ResultStore,
    level: Optional[str] = None,
    experiment_id: Optional[str] = None,
    history_key: Optional[str] = None,
    pooled: bool = False,
) -> str:
    out = io.StringIO()
    labels = store.agents()
    if history_key:
        print(f"results: {store.path}   labels: {', '.join(labels)}", file=out)
        out.write(format_history(store, history_key, level))
    else:
        if experiment_id:
            print(f"experiment {experiment_id}", file=out)
        else:
            print("pooled across all recorded runs (not a single experiment)", file=out)
        print(f"results: {store.path}   labels: {', '.join(labels)}", file=out)
        out.write(format_leaderboard(store, level, experiment_id))
    return out.getvalue()
