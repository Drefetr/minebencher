"""Durable results store.

Every batch is appended as a `run`, with its individual `games`, so scores
accumulate across time. Identity is the source fingerprint: name and version
are labels recorded alongside for humans, never keys. Editing an agent
produces a new hash and therefore a new player; renaming it does not merge
anyone else's games into it.

Losing positions are kept alongside their game so the corpus stays joined to
the result that produced it.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 4

# Columns added after the first release. Applied on open so an existing
# results.db keeps its history instead of being discarded for a new metric.
MIGRATIONS = {
    "runs": [("superseded", "INTEGER NOT NULL DEFAULT 0"),
             ("unsound_deaths", "INTEGER NOT NULL DEFAULT 0"),
             ("experiment_id", "TEXT"),
             ("baseline", "TEXT")],
    "games": [("superseded", "INTEGER NOT NULL DEFAULT 0"),
              ("fatal_certain", "INTEGER NOT NULL DEFAULT 0")],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id         TEXT    NOT NULL,
    version          TEXT    NOT NULL,
    fingerprint      TEXT    NOT NULL,
    privileged       INTEGER NOT NULL,
    description      TEXT,
    level            TEXT    NOT NULL,
    seed             INTEGER,
    games            INTEGER NOT NULL,
    wins             INTEGER NOT NULL,
    stuck            INTEGER NOT NULL,
    stalled          INTEGER NOT NULL,
    illegal          INTEGER NOT NULL,
    mean_progress    REAL,
    mean_moves       REAL,
    mean_guesses     REAL,
    games_per_second REAL,
    moves_per_second REAL,
    total_seconds    REAL,
    started_at       TEXT    NOT NULL,
    schema_version   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    game_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    won         INTEGER NOT NULL,
    opened      INTEGER NOT NULL,
    safe_cells  INTEGER NOT NULL,
    moves       INTEGER NOT NULL,
    guesses     INTEGER NOT NULL,
    seconds     REAL    NOT NULL,
    stuck       INTEGER NOT NULL,
    stalled     INTEGER NOT NULL,
    illegal     INTEGER NOT NULL,
    fatal_x     INTEGER,
    fatal_y     INTEGER,
    position    TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id, level, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_hash  ON runs(fingerprint, level, started_at);
CREATE INDEX IF NOT EXISTS idx_games_run  ON games(run_id);
"""

_ROOT = Path(__file__).resolve().parent.parent.parent if Path(__file__).resolve().parent.parent.name == "src" else Path(__file__).resolve().parent.parent
DEFAULT_DB = _ROOT / "benchmarks" / "results_cumulative.db"
if not DEFAULT_DB.exists() and (_ROOT / "results.db").exists():
    DEFAULT_DB = _ROOT / "results.db"


def wilson(wins: int, games: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a win rate.

    A point estimate alone makes small runs look decisive; ranking agents on
    50 games without an interval is how you conclude noise is progress.
    """
    if games == 0:
        return (0.0, 0.0)
    p = wins / games
    denom = 1 + z * z / games
    centre = (p + z * z / (2 * games)) / denom
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


class ResultStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_expt "
            "ON runs(experiment_id, level)")
        self.conn.commit()

    def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            existing = {r[1] for r in
                        self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in existing:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def record(self, info, stats, results, seed: Optional[int] = None,
               store_positions: bool = True,
               experiment_id: Optional[str] = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO runs (agent_id, version, fingerprint, privileged,
                   description, level, seed, games, wins, stuck, stalled,
                   illegal, superseded, unsound_deaths, mean_progress,
                   mean_moves, mean_guesses, games_per_second,
                   moves_per_second, total_seconds, started_at,
                   schema_version, experiment_id, baseline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (info.agent_id, info.version, info.fingerprint,
             int(info.privileged), info.description, stats.level, seed,
             stats.games, stats.wins, stats.stuck, stats.stalled,
             stats.illegal, stats.superseded, stats.unsound_deaths,
             stats.mean_progress, stats.mean_moves,
             stats.mean_guesses, stats.games_per_second,
             stats.moves_per_second, stats.total_seconds,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             SCHEMA_VERSION, experiment_id, getattr(info, "baseline", None)))
        run_id = cur.lastrowid

        rows = []
        for i, r in enumerate(results):
            position = None
            if store_positions and not r.won and r.view:
                position = json.dumps({"view": r.view, "mines": r.mines})
            fx, fy = r.fatal_move if r.fatal_move else (None, None)
            rows.append((run_id, i, int(r.won), r.opened, r.safe_cells,
                         r.moves, r.guesses, r.seconds, int(r.stuck),
                         int(r.stalled), r.illegal, r.superseded,
                         fx, fy, int(r.fatal_certain), position))
        cur.executemany(
            """INSERT INTO games (run_id, idx, won, opened, safe_cells, moves,
                   guesses, seconds, stuck, stalled, illegal, superseded,
                   fatal_x, fatal_y, fatal_certain, position)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        self.conn.commit()
        return run_id

    # --- queries ---------------------------------------------------------
    def latest_experiment(self) -> Optional[str]:
        row = self.conn.execute("""
            SELECT experiment_id FROM runs
            WHERE experiment_id IS NOT NULL
            ORDER BY started_at DESC, run_id DESC LIMIT 1
        """).fetchone()
        return row["experiment_id"] if row else None

    def leaderboard(self, level: Optional[str] = None,
                    include_privileged: bool = True,
                    experiment_id: Optional[str] = None) -> list[sqlite3.Row]:
        where, params = [], []
        if experiment_id:
            where.append("r.experiment_id = ?")
            params.append(experiment_id)
        if level:
            where.append("r.level = ?")
            params.append(level)
        if not include_privileged:
            where.append("r.privileged = 0")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return self.conn.execute(f"""
            SELECT r.fingerprint,
                   latest.agent_id,
                   latest.version,
                   MAX(r.privileged)              AS privileged,
                   MAX(r.baseline)                AS baseline,
                   r.level,
                   COUNT(*)                       AS runs,
                   SUM(r.games)                   AS games,
                   SUM(r.wins)                    AS wins,
                   SUM(r.illegal)                 AS illegal,
                   SUM(r.stuck)                   AS stuck,
                   SUM(r.stalled)                 AS stalled,
                   SUM(r.unsound_deaths)          AS unsound_deaths,
                   SUM(r.mean_progress * r.games) / SUM(r.games)
                                                  AS mean_progress,
                   SUM(r.mean_guesses * r.games) / SUM(r.games)
                                                  AS mean_guesses,
                   SUM(r.mean_moves * r.games)   / SUM(r.games)
                                                  AS mean_moves,
                   SUM(r.games) / SUM(r.total_seconds)
                                                  AS games_per_second,
                   MIN(r.started_at)              AS first_seen,
                   MAX(r.started_at)              AS last_seen,
                   COUNT(DISTINCT r.agent_id || '@' || r.version)
                                                  AS labels
            FROM runs r
            JOIN (
                SELECT fingerprint, agent_id, version
                FROM (
                    SELECT fingerprint, agent_id, version,
                           ROW_NUMBER() OVER (
                               PARTITION BY fingerprint
                               ORDER BY started_at DESC, run_id DESC
                           ) AS rn
                    FROM runs
                ) ranked
                WHERE rn = 1
            ) latest ON latest.fingerprint = r.fingerprint
            {clause}
            GROUP BY r.fingerprint, r.level
            ORDER BY r.level, 1.0 * SUM(r.wins) / SUM(r.games) DESC,
                     mean_progress DESC
        """, params).fetchall()

    def history(self, key: str, level: Optional[str] = None) -> list[sqlite3.Row]:
        """Runs for a fingerprint, or every fingerprint that used a label."""
        params: list = [key, key]
        clause = ""
        if level:
            clause = "AND level = ?"
            params.append(level)
        return self.conn.execute(f"""
            SELECT run_id, agent_id, version, fingerprint, level, games, wins,
                   mean_progress, mean_guesses, started_at
            FROM runs
            WHERE (fingerprint = ? OR agent_id = ?) {clause}
            ORDER BY fingerprint, started_at, run_id
        """, params).fetchall()

    def agents(self) -> list[str]:
        """Distinct display names currently in the store (not identities)."""
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT agent_id FROM runs ORDER BY agent_id")]

    def identities(self) -> list[sqlite3.Row]:
        return self.conn.execute("""
            SELECT r.fingerprint,
                   latest.agent_id, latest.version,
                   COUNT(*) AS runs, SUM(r.games) AS games,
                   MIN(r.started_at) AS first_seen, MAX(r.started_at) AS last_seen
            FROM runs r
            JOIN (
                SELECT fingerprint, agent_id, version
                FROM (
                    SELECT fingerprint, agent_id, version,
                           ROW_NUMBER() OVER (
                               PARTITION BY fingerprint
                               ORDER BY started_at DESC, run_id DESC
                           ) AS rn
                    FROM runs
                ) ranked
                WHERE rn = 1
            ) latest ON latest.fingerprint = r.fingerprint
            GROUP BY r.fingerprint
            ORDER BY first_seen
        """).fetchall()

    def fingerprints(self, agent_id: Optional[str] = None) -> list[sqlite3.Row]:
        clause, params = ("WHERE agent_id = ?", [agent_id]) if agent_id else ("", [])
        return self.conn.execute(f"""
            SELECT fingerprint, agent_id, version,
                   COUNT(*) AS runs, SUM(games) AS games,
                   MIN(started_at) AS first_seen, MAX(started_at) AS last_seen
            FROM runs {clause}
            GROUP BY fingerprint, agent_id, version
            ORDER BY fingerprint, first_seen
        """, params).fetchall()

    def delete_runs(self, fingerprint: str) -> tuple[int, int]:
        """Drop every run recorded for a source hash."""
        cur = self.conn.cursor()
        run_ids = [r[0] for r in cur.execute(
            "SELECT run_id FROM runs WHERE fingerprint = ?", (fingerprint,))]
        if not run_ids:
            return (0, 0)
        marks = ",".join("?" * len(run_ids))
        games = cur.execute(
            f"SELECT COUNT(*) FROM games WHERE run_id IN ({marks})",
            run_ids).fetchone()[0]
        cur.execute(f"DELETE FROM games WHERE run_id IN ({marks})", run_ids)
        cur.execute("DELETE FROM runs WHERE fingerprint = ?", (fingerprint,))
        self.conn.commit()
        return (len(run_ids), games)

    def label_aliases(self) -> list[sqlite3.Row]:
        """Display names that have been applied to more than one hash.

        Not a data-corruption warning: those hashes remain separate players.
        It just means the human-facing name is no longer unique.
        """
        return self.conn.execute("""
            SELECT agent_id, COUNT(DISTINCT fingerprint) AS hashes,
                   MIN(started_at) AS first_seen, MAX(started_at) AS last_seen
            FROM runs GROUP BY agent_id
            HAVING COUNT(DISTINCT fingerprint) > 1
            ORDER BY agent_id
        """).fetchall()
