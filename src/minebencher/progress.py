"""Live terminal progress for a benchmark batch.

A 100-game run can sit in `play_game` for minutes with nothing on stdout.
The status line exists so a hung agent, a slow deduction, and a healthy
batch are distinguishable without waiting for the summary table.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Optional, TextIO


def format_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds or seconds == float("inf"):
        return "?"
    total = int(round(seconds))
    if seconds > 0 and total == 0:
        return "<1s"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def outcome(result) -> str:
    if result.won:
        return "win"
    if getattr(result, "fatal_certain", False):
        return "unsound"
    if result.stalled:
        return "stalled"
    if result.timed_out:
        return "timeout"
    if result.stuck:
        return "stuck"
    return "loss"


# One cell is ~1.4% beginner, ~0.46% intermediate, ~0.26% expert.
# Zero decimal places rounds an expert 380/381 loss (99.74%) to 100%.
PROGRESS_DECIMALS = {
    "beginner": 1,
    "intermediate": 2,
    "expert": 2,
}


def progress_decimals(level: str) -> int:
    return PROGRESS_DECIMALS.get(level, 1)


def format_progress(fraction: float, level: str) -> str:
    """Format board progress as a percent that cannot read as 100% unless it is."""
    decimals = progress_decimals(level)
    if fraction >= 1:
        return f"{fraction:.{decimals}%}"
    while decimals <= 6:
        text = f"{fraction:.{decimals}%}"
        if not text.startswith("100"):
            return text
        decimals += 1
    return f"{fraction:.{decimals}%}"


def format_status(
    *,
    current: int,
    total: int,
    level: str,
    wins: int,
    losses: int,
    stuck: int,
    timed_out: int,
    stalled: int,
    unsound: int,
    rate: float,
    eta: Optional[float],
    phase: str,
    last_outcome: Optional[str] = None,
    last_progress: Optional[float] = None,
    last_guesses: Optional[int] = None,
    mean_guesses: Optional[float] = None,
    opened: Optional[int] = None,
    safe_cells: Optional[int] = None,
    moves: Optional[int] = None,
    guesses: Optional[int] = None,
    game_seconds: Optional[float] = None,
) -> str:
    tallies = [f"{wins}W", f"{losses}L"]
    if stuck:
        tallies.append(f"{stuck}S")
    if timed_out:
        tallies.append(f"{timed_out}T")
    if stalled:
        tallies.append(f"{stalled}X")
    if unsound:
        tallies.append(f"{unsound}U")
    parts = [
        f"{current}/{total} {level}",
        " ".join(tallies),
    ]
    if mean_guesses is not None:
        parts.append(f"guesses {mean_guesses:.1f}")
    if rate > 0:
        parts.append(f"{rate:.1f} g/s")
    if eta is not None:
        parts.append(f"ETA {format_duration(eta)}")

    if phase == "playing":
        detail = "playing"
        if opened is not None and safe_cells:
            detail += f" {opened}/{safe_cells}"
            detail += f" {format_progress(opened / safe_cells, level)}"
        if moves:
            detail += f" {moves}mv"
        if guesses:
            detail += f" {guesses} guess"
        if game_seconds is not None:
            detail += f" {game_seconds:.1f}s"
        parts.append(detail)
    elif phase == "starting":
        parts.append("starting")
    elif last_outcome is not None:
        last = f"last={last_outcome}"
        if last_progress is not None:
            last += f" {format_progress(last_progress, level)}"
        if last_guesses:
            last += f" {last_guesses} guess"
        parts.append(last)

    return "    " + "  ".join(parts)


class BatchProgress:
    """Rewrite a single status line on a TTY; emit snapshots otherwise."""

    def __init__(self, games: int, level: str, every: int = 1,
                 stream: Optional[TextIO] = None):
        self.total = games
        self.level = level
        self.every = max(0, every)
        self._stream = stream if stream is not None else sys.stdout
        self.tty = hasattr(self._stream, "isatty") and self._stream.isatty()
        self.enabled = self.every > 0
        self.live = self.enabled and self.tty

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = time.perf_counter()
        self._game_started = self._started
        self._current = 0
        self._phase = "idle"
        self._results: list = []
        self._moves = 0
        self._guesses = 0
        self._opened: Optional[int] = None
        self._safe: Optional[int] = None
        self._last_len = 0
        self._last_render = 0.0

    def start(self) -> None:
        if not self.enabled:
            return
        self._emit(
            f"    running {self.total} {self.level} game(s)...",
            newline=True, locked=False)
        if self.live:
            self._thread = threading.Thread(
                target=self._heartbeat, daemon=True)
            self._thread.start()

    def begin_game(self, index: int) -> None:
        if not self.live:
            return
        with self._lock:
            self._current = index + 1
            self._phase = "starting"
            self._game_started = time.perf_counter()
            self._moves = 0
            self._guesses = 0
            self._opened = None
            self._safe = None
            self._render(force=True)

    def on_move(self, moves: int, snap, guesses: int = 0) -> None:
        if not self.live:
            return
        opened = getattr(snap, "opened", None)
        safe = getattr(snap, "safe_cells", None)
        with self._lock:
            self._phase = "playing"
            self._moves = moves
            self._guesses = guesses
            self._opened = opened
            self._safe = safe
            self._render(force=False)

    def finish_game(self, result) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._results.append(result)
            self._phase = "done"
            self._current = len(self._results)
            commit = (
                self._current % self.every == 0
                or self._current == self.total)
            if self.live or commit:
                self._render(force=True, newline=commit)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        with self._lock:
            if self._last_len:
                self._stream.write("\n")
                self._stream.flush()
                self._last_len = 0

    def _heartbeat(self) -> None:
        while not self._stop.wait(0.25):
            with self._lock:
                if self._phase in ("starting", "playing"):
                    self._render(force=False)

    def _counts(self):
        wins = losses = stuck = timed_out = stalled = unsound = 0
        for result in self._results:
            if result.won:
                wins += 1
            elif result.stuck:
                stuck += 1
            elif result.timed_out:
                timed_out += 1
            elif result.stalled:
                stalled += 1
            else:
                losses += 1
            if getattr(result, "fatal_certain", False):
                unsound += 1
        return wins, losses, stuck, timed_out, stalled, unsound

    def _render(self, force: bool = False, newline: bool = False) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if not force and not newline and now - self._last_render < 0.2:
            return
        self._last_render = now

        done = len(self._results)
        elapsed = now - self._started
        rate = done / elapsed if done and elapsed > 0 else 0.0
        remaining = self.total - done
        eta = remaining / rate if rate > 0 and remaining else None
        wins, losses, stuck, timed_out, stalled, unsound = self._counts()

        last_outcome = last_progress = last_guesses = None
        mean_guesses = None
        if self._results:
            last = self._results[-1]
            last_outcome = outcome(last)
            last_progress = last.progress
            last_guesses = getattr(last, "guesses", None)
            total_guesses = sum(getattr(r, "guesses", 0) for r in self._results)
            mean_guesses = total_guesses / done

        phase = self._phase
        current = self._current if phase in ("starting", "playing") else done
        game_seconds = None
        if phase in ("starting", "playing"):
            game_seconds = now - self._game_started
            if phase == "starting" and game_seconds >= 0.5:
                phase = "playing"

        line = format_status(
            current=current, total=self.total, level=self.level,
            wins=wins, losses=losses, stuck=stuck, timed_out=timed_out,
            stalled=stalled, unsound=unsound, rate=rate, eta=eta,
            phase=phase, last_outcome=last_outcome,
            last_progress=last_progress, last_guesses=last_guesses,
            mean_guesses=mean_guesses, opened=self._opened,
            safe_cells=self._safe, moves=self._moves,
            guesses=self._guesses, game_seconds=game_seconds,
        )
        self._emit(line, newline=newline, locked=True)

    def _emit(self, line: str, newline: bool, locked: bool) -> None:
        def write() -> None:
            if self.live and not newline:
                pad = max(0, self._last_len - len(line))
                self._stream.write("\r" + line + (" " * pad))
                self._stream.flush()
                self._last_len = len(line)
                return
            if self._last_len:
                self._stream.write("\r" + (" " * self._last_len) + "\r")
                self._last_len = 0
            self._stream.write(line + "\n")
            self._stream.flush()

        if locked:
            write()
        else:
            with self._lock:
                write()
