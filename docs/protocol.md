# Minebencher Agent Protocol & Interface Specification

This document is the authoritative reference for the `minebencher.agent` API, data types, and execution semantics.

---

## 1. The Trust Boundary

Minebencher enforces an epistemic trust boundary between the privileged runner and candidate solvers:

```
+-------------------------------------------------------------+
|               Privileged Harness (minebencher/)             |
|   - Holds Win32 process handle                              |
|   - Reads process memory & tracks ground truth              |
|   - Enforces game rules & detects deduction soundness bugs  |
+------------------------------+------------------------------+
                               |
                   Observation | (immutable, mines stripped)
                               v
+-------------------------------------------------------------+
|                 Contributor Space (agents/)                 |
|   - Autonomous solvers implementing the Agent protocol      |
|   - Receives only player-visible board state                |
|   - Returns Move(action, x, y, certain)                     |
+-------------------------------------------------------------+
```

Solvers receive an immutable `Observation` object. All mine layout bits and process handles are completely stripped. Honesty is guaranteed structurally by type design.

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
| `elapsed` | `int` | Seconds on the timer. |
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

### Move Batching & Cascades
An agent may return a batch of multiple moves from `act()`. The harness processes them in order:
1. Validates move format and bounds.
2. If an open action triggers a recursive cascade that reveals other cells planned in the same batch, subsequent moves targeting those newly uncovered cells are classified as **`superseded`** (not counted as mistakes or illegal).
3. Moves with invalid coordinates or unknown actions are classified as **`illegal`**.

### Resignation
If an agent cannot find any safe move and covered cells remain, returning an empty list `[]` signals resignation. The game is scored as **`stuck`** rather than a loss.

### First-Click Safety
`WINMINE.EXE` relocates any mine landed on by the first click to the top-left available empty cell. Thus, the opening move can never trigger an explosion.

---

## 5. Identity & Fingerprinting

Solvers are identified by **SHA-256 fingerprint** of their source files:
- `agent_id` and `version` are purely display labels.
- Modifying a single character of code changes the hash and creates a fresh player in `results.db`.
- Renaming an agent file or class preserves its hash and keeps historical stats joined.
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

The white-noise baseline [agents/random.py](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/agents/random.py) demonstrates the complete agent contract in minimal code:

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
