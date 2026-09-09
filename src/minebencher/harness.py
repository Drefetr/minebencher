"""Self-play harness: run an agent over many games and score it.

The harness sits on the privileged side of the boundary. It holds the process
handle and the oracle; the agent gets only an `Observation`. Beyond win rate
it records, for every loss, the exact position at the moment of death, so a
future solver can be replayed against a corpus of real failures and told
whether each one was a forced guess or a missed deduction.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import layout as L
from .agent import COVERED, AgentInfo, Move, Observation, describe
from .reader import Snapshot
from .session import Session, Stalled


def observe(snap: Snapshot) -> Observation:
    """Strip the oracle and freeze what remains."""
    return Observation(
        width=snap.width, height=snap.height,
        mine_total=snap.mine_total, mines_left=snap.mines_left,
        elapsed=snap.elapsed, opened=snap.opened, safe_cells=snap.safe_cells,
        view=tuple(tuple(int(v) for v in row) for row in snap.view),
    )


@dataclass
class GameResult:
    won: bool
    stuck: bool               # agent returned no move while cells remained
    stalled: bool             # the game stopped responding
    illegal: int              # malformed moves: bad action or out of bounds
    superseded: int           # well-formed, but the cell was already resolved
    opened: int
    safe_cells: int
    moves: int
    guesses: int              # moves the agent did not claim as certain
    seconds: float
    fatal_move: Optional[tuple[int, int]] = None
    # True when the agent died on a move it asserted was certain. For a
    # sound solver this can never happen, so any occurrence is a deduction
    # bug rather than bad luck.
    fatal_certain: bool = False
    view: list[list[int]] = field(default_factory=list)
    mines: list[list[bool]] = field(default_factory=list)

    @property
    def progress(self) -> float:
        return self.opened / self.safe_cells if self.safe_cells else 0.0


def _classify(obs: Observation, mv: Move) -> str:
    """Separate agent error from ordinary staleness.

    An agent that batches several deduced moves is behaving correctly, but a
    cascade may resolve some of them before their turn arrives. That is not a
    mistake, so it is counted separately from genuinely malformed moves.
    """
    if mv.action not in ("open", "flag") or not obs.in_bounds(mv.x, mv.y):
        return "illegal"
    if obs.at(mv.x, mv.y) != COVERED:
        return "superseded"
    return "ok"


def play_game(session: Session, agent, level: Optional[str] = None,
              max_moves: int = 5000, capture: bool = True) -> GameResult:
    privileged = getattr(agent, "privileged", False)
    snap = session.reset(level)
    started = time.perf_counter()
    moves = guesses = illegal = superseded = 0
    fatal: Optional[tuple[int, int]] = None
    fatal_certain = False
    stuck = stalled = False

    while not snap.over and moves < max_moves:
        obs = observe(snap)
        decided = agent.act(obs, snap.mines) if privileged else agent.act(obs)
        if not decided:
            stuck = True
            break

        for mv in decided:
            if snap.over or moves >= max_moves:
                break
            verdict = _classify(observe(snap), mv)
            if verdict == "illegal":
                illegal += 1
                continue
            if verdict == "superseded":
                superseded += 1
                continue
            moves += 1
            if not mv.certain:
                guesses += 1
            try:
                if mv.action == "open":
                    snap = session.open(mv.x, mv.y)
                    if snap.status == L.Status.LOST and fatal is None:
                        fatal = (mv.x, mv.y)
                        fatal_certain = mv.certain
                else:
                    snap = session.flag(mv.x, mv.y)
            except Stalled:
                stalled = True
                break
        if stalled:
            break

    return GameResult(
        won=snap.status == L.Status.WON,
        stuck=stuck, stalled=stalled, illegal=illegal, superseded=superseded,
        opened=snap.opened, safe_cells=snap.safe_cells,
        moves=moves, guesses=guesses,
        seconds=time.perf_counter() - started,
        fatal_move=fatal, fatal_certain=fatal_certain,
        view=[[int(v) for v in row] for row in snap.view] if capture else [],
        mines=snap.mines if capture else [],
    )


@dataclass
class Stats:
    agent: str          # "agent_id@version"
    agent_id: str
    version: str
    fingerprint: str
    level: str
    privileged: bool
    games: int
    wins: int
    stuck: int
    stalled: int
    illegal: int
    superseded: int
    unsound_deaths: int    # died on a move asserted certain: a soundness bug
    win_rate: float
    mean_progress: float
    median_progress: float
    mean_moves: float
    mean_guesses: float
    games_per_second: float
    moves_per_second: float
    total_seconds: float

    def table(self) -> str:
        lines = []
        if self.privileged:
            lines.append("  *** PRIVILEGED RUN: agent was given the mine "
                         "oracle. Not a real result. ***")
        lines += [
            f"  agent            {self.agent}  [{self.fingerprint}]",
            f"  level            {self.level}",
            f"  games            {self.games}",
            f"  wins             {self.wins}  ({self.win_rate:.1%})",
            f"  stuck / stalled  {self.stuck} / {self.stalled}",
            f"  illegal moves    {self.illegal}"
            f"   (superseded {self.superseded})",
            f"  unsound deaths   {self.unsound_deaths}"
            + ("   <-- deduction bug: died on a 'certain' move"
               if self.unsound_deaths else ""),
            f"  mean progress    {self.mean_progress:.1%}"
            f"   (median {self.median_progress:.1%})",
            f"  mean moves       {self.mean_moves:.1f}"
            f"   (guesses {self.mean_guesses:.1f})",
            f"  throughput       {self.games_per_second:.1f} games/s, "
            f"{self.moves_per_second:.0f} moves/s"
            f"   over {self.total_seconds:.1f}s",
        ]
        return "\n".join(lines)


def run_batch(session: Session, agent, games: int, level: str,
              log_losses: Optional[Path] = None, progress_every: int = 0,
              capture: bool = True) -> tuple[Stats, list[GameResult]]:
    info = describe(agent)
    results: list[GameResult] = []
    started = time.perf_counter()

    handle = open(log_losses, "w", encoding="utf-8") if log_losses else None
    try:
        for i in range(games):
            r = play_game(session, agent, level,
                          capture=capture or bool(handle))
            results.append(r)
            if handle and not r.won:
                record = asdict(r)
                record["agent"] = info.label
                record["fingerprint"] = info.fingerprint
                record["level"] = level
                handle.write(json.dumps(record) + "\n")
            if progress_every and (i + 1) % progress_every == 0:
                wins = sum(1 for x in results if x.won)
                print(f"    {i + 1}/{games} games, {wins} wins "
                      f"({wins / len(results):.1%})", flush=True)
    finally:
        if handle:
            handle.close()

    elapsed = time.perf_counter() - started
    progress = [r.progress for r in results]
    total_moves = sum(r.moves for r in results)
    stats = Stats(
        agent=info.label, agent_id=info.agent_id, version=info.version,
        fingerprint=info.fingerprint, level=level,
        privileged=info.privileged,
        games=len(results),
        wins=sum(1 for r in results if r.won),
        stuck=sum(1 for r in results if r.stuck),
        stalled=sum(1 for r in results if r.stalled),
        illegal=sum(r.illegal for r in results),
        superseded=sum(r.superseded for r in results),
        unsound_deaths=sum(1 for r in results if r.fatal_certain),
        win_rate=sum(1 for r in results if r.won) / len(results),
        mean_progress=statistics.fmean(progress),
        median_progress=statistics.median(progress),
        mean_moves=statistics.fmean(r.moves for r in results),
        mean_guesses=statistics.fmean(r.guesses for r in results),
        games_per_second=len(results) / elapsed,
        moves_per_second=total_moves / elapsed,
        total_seconds=elapsed,
    )
    return stats, results


def run_and_record(session: Session, agent, games: int, level: str,
                   store=None, seed: Optional[int] = None,
                   experiment_id: Optional[str] = None, **kwargs):
    """Run a batch and append it to the durable store."""
    stats, results = run_batch(session, agent, games, level, **kwargs)
    if store is not None:
        stores = store if isinstance(store, (list, tuple)) else [store]
        for s in stores:
            s.record(describe(agent), stats, results, seed=seed,
                     experiment_id=experiment_id)
    return stats, results
