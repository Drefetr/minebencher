# Agent Authoring Guide

A quickstart guide for writing and benchmarking autonomous Minesweeper solvers in Minebencher.

For the exhaustive API reference and execution semantics, see [docs/protocol.md](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/docs/protocol.md).

---

## 1. The Agent Contract

Drop a Python file into `agents/` implementing the `Agent` protocol:

```python
from typing import Protocol, runtime_checkable
from minebencher.agent import Observation, Move

@runtime_checkable
class Agent(Protocol):
    agent_id: str   # Display name (e.g. "my_solver")
    version: str    # Revision label (e.g. "1.0")

    def act(self, obs: Observation) -> list[Move]:
        """Inspect the current observation and return planned moves."""
        ...
```

If your agent uses random numbers, accept an optional `seed: int | None = None` in `__init__` (the harness will pass experiment seeds automatically).

---

## 2. Quickstart: Building a Solver

### Step 1: Copy the Template
Copy `agents/_template.py` to a new filename without a leading underscore (files starting with `_` or `.` are skipped by discovery):

```bash
cp agents/_template.py agents/my_solver.py
```

### Step 2: Implement the Agent Logic
Inspect the incoming `Observation` and return a list of planned `Move`s.

For a concrete, working example of how to implement the agent protocol, inspect the **Random Agent Walkthrough** in [docs/protocol.md](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/docs/protocol.md#7-canonical-reference-implementation-random-agent) (referencing [agents/random.py](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/agents/random.py)).

### Step 3: Verify Discovery

```bash
python src/tools/agents.py
```

### Step 4: Benchmark Against Baselines

```bash
python src/tools/bench.py --only my_solver --games 100 --level beginner
python src/tools/report.py
```

---

## 3. Critical Rules & Mechanics

- **Soundness & Guesses**: Set `certain=True` only when a move is mathematically guaranteed. If an agent dies on a move marked `certain=True`, the harness records an **Unsound Death** (a solver bug). When guessing, set `certain=False`.
- **Resignation**: Returning `[]` when covered cells remain records the game as **`stuck`** rather than a loss.
- **Content-Addressed Identity**: Solvers are identified in `results.db` by the SHA-256 fingerprint of their source file. Editing any line of code produces a fresh identity, while renaming preserves history.
- **Baselines**: Leave `baseline = None` (the default). The roles `floor` (`agents/random.py`) and `ceiling` (`agents/oracle.py`) are reserved control baselines.

---

## 4. Deep Dive Documentation

- **[docs/protocol.md](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/docs/protocol.md)**: Exhaustive method documentation for `Observation`, `Move`, and cell constants.
- **[docs/benchmarking.md](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/docs/benchmarking.md)**: Statistical evaluation, Wilson score 95% CIs, and loss replay.
- **[docs/tools.md](file:///c:/Users/Drefetr/Downloads/Minesweeper-Windows-XP/docs/tools.md)**: Full options reference for `bench.py`, `report.py`, `replay.py`, etc.
