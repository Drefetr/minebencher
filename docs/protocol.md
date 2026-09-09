# Minebencher Agent Protocol & Interface Specification

This document is the authoritative reference for the `minebencher.agent` API, data types, and execution semantics.

---

## 1. The Agent API Boundary

Minebencher exposes an epistemic API boundary between the privileged runner and candidate solvers:

```
+-------------------------------------------------------------+
|               Privileged Harness (minebencher/)             |
|   - Holds Win32 process handle                              |
|   - Reads process memory & tracks ground truth              |
|   - Enforces game rules & detects deduction soundness bugs  |
+------------------------------+------------------------------+
                               |
           JSON-over-stdio IPC | (Observation serialised,
                               |  mines stripped)
                               v
+-------------------------------------------------------------+
|              Isolated Agent Process (agents/)               |
|   - Each agent runs in a dedicated child Python process     |
|   - Receives only player-visible board state                |
|   - Returns Move(action, x, y, certain)                     |
+-------------------------------------------------------------+
```

Each agent runs in a dedicated child process (`minebencher.worker`), communicating with the harness via a JSON-over-stdio protocol. The agent receives serialised `Observation` objects with mine layout bits stripped. This subprocess boundary provides fault isolation — a crashing or hanging agent cannot take down the harness — but is not a security sandbox for hostile submissions; agent code is still trusted Python.

---

## 2. The Agent Protocol

Every solver in `agents/` must implement `Agent`:

```python
from typing import Protocol, runtime_checkable
from minebencher.agent import Observation, Move

@runtime_checkable
class Agent(Protocol):
    agent_id: str   # Display name (e.g., "deductive")
    version: str    # Revision label (e.g., "1.0")

    def act(self, obs: Observation) -> list[Move]:
        """Inspect the observation and return a list of planned actions."""
        ...
```

### Constructor Seeding (Optional)
If your solver uses random numbers, accept an optional `seed: int | None = None` in `__init__`:
```python
class MyAgent:
    agent_id = "my_solver"
    version = "1.0"

    def __init__(self, seed: int | None = None):
        import random
        self.rng = random.Random(seed)

    def act(self, obs: Observation) -> list[Move]:
        ...
```
The harness inspects constructor signatures and passes the experiment seed automatically.

---

## 3. Data Types & API Reference

All agent types and constants are exported from `minebencher.agent`:

```python
from minebencher.agent import (
    COVERED,
    FLAGGED,
    QUESTION,
    Move,
    Observation,
    describe,
)
```

### 3.1 Cell Constants

| Constant | Value | Glyph | Description |
| :--- | :---: | :---: | :--- |
| `COVERED` | `-1` | `.` | Unopened, unflagged cell. |
| `FLAGGED` | `-2` | `F` | Flagged cell. |
| `QUESTION` | `-3` | `?` | Marked with a question mark. |
| `0` to `8` | `0`..`8` | `' '` or `'1'`..`'8'` | Uncovered cell showing adjacent mine count (`0` is blank). |

### 3.2 `Move` NamedTuple

Represents an intended action:

```python
class Move(NamedTuple):
    action: str            # "open" or "flag"
    x: int                 # 0-indexed column (0 <= x < obs.width)
    y: int                 # 0-indexed row    (0 <= y < obs.height)
    certain: bool = False  # True if mathematically proven; False if guessing
```

- **`action`**: `"open"` (left-click) or `"flag"` (right-click).
- **`x, y`**: 0-indexed column and row coordinates.
- **`certain`**: Must be `True` only when deduced with 100% mathematical certainty. Set to `False` when making a probabilistic guess.

> [!IMPORTANT]
> **Soundness Guarantee & Unsound Deaths**:
> If an agent marks a move as `certain=True` and opening that cell triggers an explosion, the harness records an **Unsound Death**. This immediately flags an algorithmic deduction bug in the solver rather than unlucky RNG.

### 3.3 `Observation` Dataclass

An immutable snapshot representing the board state:

#### Fields

| Field | Type | Description |
| :--- | :--- | :--- |
| `width` | `int` | Board width in columns ($W$). |
| `height` | `int` | Board height in rows ($H$). |
| `mine_total` | `int` | Total mines placed on the board. |
| `mines_left` | `int` | Displayed counter (`mine_total - flags_placed`). |
| `elapsed` | `int` | Seconds on Winmine's timer, capped at 999. |
| `opened` | `int` | Count of cells uncovered so far. |
| `safe_cells` | `int` | Win target ($W \times H - \text{mine_total}$). |
| `view` | `tuple[tuple[int, ...], ...]` | 2D board state accessed as `obs.view[y][x]`. |

#### Methods

- **`obs.at(x: int, y: int) -> int`**: Value at `(x, y)`.
- **`obs.in_bounds(x: int, y: int) -> bool`**: Returns `True` if `0 <= x < width` and `0 <= y < height`.
- **`obs.cells() -> Iterator[tuple[int, int, int]]`**: Yields `(x, y, value)` across the whole board.
- **`obs.covered() -> list[tuple[int, int]]`**: Returns all `(x, y)` coordinates where `value == COVERED`.
- **`obs.numbered() -> list[tuple[int, int, int]]`**: Returns `(x, y, count)` for all open cells with `count > 0`. This is the complete active constraint surface.
- **`obs.neighbours(x: int, y: int) -> Iterator[tuple[int, int, int]]`**: Yields up to 8 adjacent `(nx, ny, value)` cells.
- **`obs.render() -> str`**: Returns an ASCII text rendering of the visible board.

---

## 4. Execution Semantics & Game Rules

### Subprocess Isolation
Agents are never imported into the harness process. The registry discovers agents by spawning a short-lived child process (`minebencher.worker`) for each candidate file; at benchmark time, each agent gets its own long-lived worker process. Communication uses a line-delimited JSON protocol over stdin/stdout:

1. **Startup**: The worker loads the agent module, sends a `{"type": "ready", ...}` message with identity metadata, and waits.
2. **Per-turn**: The harness sends `{"type": "act", "observation": {...}}`. The worker calls `agent.act()`, encodes the returned moves, and replies with `{"type": "moves", "moves": [...]}`. Agent `print()` calls are redirected to stderr so they cannot corrupt the protocol stream.
3. **Shutdown**: The harness sends `{"type": "close"}`; the worker exits cleanly. If the agent hangs, the harness terminates the process after a timeout.
4. **Timeouts**: Each `act` call is given a deadline equal to the remaining game time (999 − elapsed seconds). Exceeding it raises `AgentTimedOut` on the harness side.

### Move Batching & Cascades
An agent may return a batch of multiple moves from `act()`. The harness processes them in order:
1. Validates move format and bounds.
2. If an open action triggers a recursive cascade that reveals other cells planned in the same batch, subsequent moves targeting those newly uncovered cells are classified as **`superseded`** (not counted as mistakes or illegal).
3. Moves with invalid coordinates or unknown actions are classified as **`illegal`**.

### Resignation
If an agent cannot find any safe move and covered cells remain, returning an empty list `[]` signals resignation. The game is scored as **`stuck`** rather than a loss.

If a non-empty batch contains no move the harness can apply, the game is also
`stuck`; retrying against the unchanged observation could never advance it.

### Time Limit
The harness stops a game when Winmine's own timer reaches 999 seconds and
records it separately as **`timed out`**.

### First-Click Safety
`WINMINE.EXE` relocates any mine landed on by the first click to the top-left available empty cell. Thus, the opening move can never trigger an explosion.

---

## 5. Identity & Fingerprinting

Solvers are identified by **SHA-256 fingerprint** of their source files:
- `agent_id` and `version` are purely display labels.
- Modifying a single character of code changes the hash and creates a fresh player in `results.db`.
- The source filename contributes to the hash, so renaming an agent file creates a new identity. Renaming only its class changes the hash because it changes the source contents.
- Package agents (a directory with `__init__.py`) are hashed across all `.py` files in that package.

---

## 6. Baselines & Privileged Agents

Every valid benchmark session executes two baseline controls:
- **Floor (`baseline = "floor"`)**: `agents/random.py` (`RandomAgent`).
- **Ceiling (`baseline = "ceiling"`)**: `agents/oracle.py` (`OracleAgent`).

Candidate solvers must leave `baseline = None` (the default).

### Privileged Agents
Solvers setting `privileged = True` receive the mine boolean matrix `mines: list[list[bool]]`. Runs by privileged agents are permanently flagged and excluded from competitive rankings.

---

## 7. Canonical Reference Implementation: Random Agent

The white-noise baseline [agents/random.py](../agents/random.py) demonstrates the complete agent contract in minimal code:

```python
"""White-noise baseline: the floor every real agent must clear."""
import random

from minebencher.agent import Move, Observation


class RandomAgent:
    """White noise: open a uniformly random covered cell, never flag."""

    agent_id = "random"
    version = "1.0"
    baseline = "floor"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def act(self, obs: Observation) -> list[Move]:
        cells = obs.covered()
        if not cells:
            return []
        x, y = self.rng.choice(cells)
        return [Move("open", x, y, certain=False)]
```

### Key Elements Illustrated:
1. **Agent Metadata**: Defines `agent_id` and `version` attributes. Candidate solvers omit `baseline = "floor"`.
2. **Deterministic Seeding**: `__init__(seed=...)` accepts an optional random seed, which the benchmark harness supplies automatically for reproducible experiments.
3. **Observation Querying**: `obs.covered()` inspects the current board snapshot to retrieve all unrevealed coordinates `(x, y)`.
4. **Move Return**: Returns a list of `Move("open", x, y, certain=False)` instances. Because random moves are probabilistic guesses, setting `certain=False` prevents penalizing the solver for unsound deduction deaths when mines explode.
5. **Resignation**: Returns `[]` when no valid moves remain, cleanly terminating the run.
