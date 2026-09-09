"""The agent-facing boundary.

Terminology, which the code follows strictly:

  agent   The entity on the far side of the interface. It has a stable
          identity, it is what gets scored, and it is what a contributor
          writes. The harness knows about agents and nothing else.
  policy  How an agent decides a move. Purely internal. An agent may hold
          many policies and switch between them per level, per board, or per
          position; that is invisible to the harness by design, and it is
          why the harness never names or scores a policy.

An agent receives an `Observation` and returns `Move`s. That is the supported
interface. An Observation is an immutable value object built from a snapshot
with the mine layout stripped out: it exposes no process handle, reader, or
`.mines` field. Each agent runs in a dedicated child process
(`minebencher.worker`), communicating via JSON-over-stdio; agent code is
never imported into the harness process.

The harness keeps the oracle on its side of the boundary and uses it purely
for scoring. `PrivilegedAgent` is the single, explicit exception, and any run
involving one is flagged so its numbers can never be mistaken for a result.
"""
from __future__ import annotations

import hashlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, NamedTuple, Protocol, runtime_checkable

from . import layout as L

# Re-exported so agent code need not import the memory layout module.
COVERED = int(L.View.COVERED)
FLAGGED = int(L.View.FLAGGED)
QUESTION = int(L.View.QUESTION)

NEIGHBOUR_OFFSETS = tuple((dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                          if (dx, dy) != (0, 0))


class Move(NamedTuple):
    action: str            # "open" or "flag"
    x: int
    y: int
    certain: bool = False  # False marks a deliberate guess, for scoring


@dataclass(frozen=True)
class Observation:
    """Exactly what a human player could see."""

    width: int
    height: int
    mine_total: int
    mines_left: int
    elapsed: int
    opened: int
    safe_cells: int
    view: tuple[tuple[int, ...], ...]

    def at(self, x: int, y: int) -> int:
        return self.view[y][x]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def cells(self) -> Iterator[tuple[int, int, int]]:
        for y in range(self.height):
            for x in range(self.width):
                yield x, y, self.view[y][x]

    def covered(self) -> list[tuple[int, int]]:
        return [(x, y) for x, y, v in self.cells() if v == COVERED]

    def numbered(self) -> list[tuple[int, int, int]]:
        """Open cells showing a non-zero count: the entire constraint set."""
        return [(x, y, v) for x, y, v in self.cells() if v > 0]

    def neighbours(self, x: int, y: int) -> Iterator[tuple[int, int, int]]:
        for dx, dy in NEIGHBOUR_OFFSETS:
            nx, ny = x + dx, y + dy
            if self.in_bounds(nx, ny):
                yield nx, ny, self.view[ny][nx]

    def render(self) -> str:
        glyph = {COVERED: ".", FLAGGED: "F", QUESTION: "?"}
        rows = []
        for y in range(self.height):
            rows.append("".join(
                glyph.get(v, " " if v == 0 else str(v) if 0 <= v <= 8 else "!")
                for v in self.view[y]))
        return "\n".join(rows)


@runtime_checkable
class Agent(Protocol):
    agent_id: str    # human-facing name; used to invoke, not to score
    version: str     # human-facing revision label

    def act(self, obs: Observation) -> list[Move]:
        ...


class PrivilegedAgent(Protocol):
    """Harness-internal only. Receives the oracle; results are flagged."""

    agent_id: str
    version: str
    privileged: bool

    def act(self, obs: Observation, mines: list[list[bool]]) -> list[Move]:
        ...


@dataclass(frozen=True)
class AgentInfo:
    agent_id: str
    version: str
    fingerprint: str
    privileged: bool
    description: str
    baseline: str | None = None  # "floor" or "ceiling"; None = a candidate

    @property
    def label(self) -> str:
        return f"{self.agent_id}@{self.version}"


def _fingerprint(agent) -> str:
    """Hash everything the agent's behaviour could depend on.

    This is the agent's identity in the results store. Name and version are
    labels for humans; two files that share a name but differ in content are
    two players. The filename is deliberately part of the digest, so renaming
    a source file also creates a new identity.

    Hashing only the class body (inspect.getsource on the type) is too weak:
    module-level helpers, imports and sibling files in a package all affect
    behaviour. So hash the whole source file, or every .py in the directory
    when the agent ships as a package.
    """
    module = sys.modules.get(type(agent).__module__)
    path = getattr(module, "__file__", None)
    try:
        if path:
            source = Path(path)
            files = (sorted(source.parent.rglob("*.py"))
                     if source.name == "__init__.py" else [source])
            digest = hashlib.sha256()
            for f in files:
                digest.update(f.name.encode())
                digest.update(f.read_bytes())
            return digest.hexdigest()[:12]
        return hashlib.sha256(
            inspect.getsource(type(agent)).encode()).hexdigest()[:12]
    except (OSError, TypeError):
        return "unknown"


def describe(agent) -> AgentInfo:
    """Identity plus display labels.

    Scores accumulate against the source fingerprint. `agent_id` and
    `version` are recorded so reports can show a name; they are never the
    key that joins games together.
    """
    fingerprint = getattr(agent, "fingerprint", None) or _fingerprint(agent)
    privileged = bool(getattr(agent, "privileged", False))
    baseline = getattr(agent, "baseline", None)
    if baseline not in ("floor", "ceiling", None):
        baseline = None
    if privileged and baseline is None:
        baseline = "ceiling"
    return AgentInfo(
        agent_id=getattr(agent, "agent_id", type(agent).__name__.lower()),
        version=str(getattr(agent, "version", "0")),
        fingerprint=fingerprint,
        privileged=privileged,
        description=(getattr(agent, "description", None) or
                     (inspect.getdoc(type(agent)) or "").split("\n")[0]),
        baseline=baseline,
    )
