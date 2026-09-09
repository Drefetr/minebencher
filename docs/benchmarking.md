# Minebencher Benchmarking, Statistics & Persistence

This document describes the evaluation methodology, statistical rigor, database architecture, and regression mechanics in Minebencher.

---

## 1. Experimental Methodology

Benchmarking autonomous Minesweeper agents requires controlled, identical conditions:

1. **Mandatory Controls**:
   - Every experiment executes a **Floor** (`agents/random.py`, white noise) and a **Ceiling** (`agents/oracle.py`, perfect play).
   - Candidate solvers are evaluated within the exact same session, difficulty levels, and sample size ($N$).
   - If baselines are missing or modified, the runner raises `IncompleteBaselines` and halts.

2. **Metrics Evaluated**:
   - **Win Rate (%)**: Raw percentage of games successfully completed.
   - **95% Wilson Confidence Interval**: Quantifies uncertainty for small sample sizes.
   - **Mean Progress (%)**: Percentage of safe cells uncovered before loss or stall.
   - **Mean Guesses**: Average number of moves executed with `certain=False`.
   - **Unsound Deaths**: Count of deaths occurring on moves asserted as `certain=True`.
   - **Throughput**: Measured in games per second and moves per second.

---

## 2. Statistical Rigour: Wilson Score Intervals

Point estimates on small batches can be misleading—a candidate winning 2/10 games may appear to have a 20% win rate when its true capability is within $[5.7\%, 51.0\%]$.

Minebencher calculates the two-sided **95% Wilson Score Interval** ($z = 1.96$):

$$p \pm z \sqrt{\frac{p(1-p)}{n} + \frac{z^2}{4n^2}} \Big/ \left(1 + \frac{z^2}{n}\right)$$

This ensures ranking tables provide realistic bounds and prevents statistical noise from being mistaken for algorithmic progress.

---

## 3. Database Architecture & Storage (`benchmarks/`)

All benchmark runs persist in SQLite across two relational tables. Each run is archived in `benchmarks/<experiment_id>/` with an isolated database (`results_<id>.db`) and text report (`report_<id>.txt`), while results are unioned into the cumulative database (`benchmarks/results_cumulative.db`) and `benchmarks/report_cumulative.txt`.

### 3.1 `runs` Table
Captures batch-level summaries for an agent execution:
- `run_id`: Primary key.
- `fingerprint`: 12-hex SHA-256 source hash (the durable identity key).
- `agent_id`, `version`, `description`: Human-facing display labels.
- `privileged`, `baseline`: Integrity flags.
- `level`: Difficulty (`beginner`, `intermediate`, `expert`).
- `games`, `wins`, `stuck`, `stalled`, `illegal`, `superseded`, `unsound_deaths`.
- `mean_progress`, `mean_moves`, `mean_guesses`, `games_per_second`, `moves_per_second`.
- `started_at`, `experiment_id`.

### 3.2 `games` Table
Stores granular details for every game in a run:
- `game_id`: Primary key.
- `run_id`: Foreign key to `runs(run_id)`.
- `won`, `opened`, `safe_cells`, `moves`, `guesses`, `seconds`.
- `fatal_x`, `fatal_y`: Cell coordinate that ended the game.
- `fatal_certain`: Whether the fatal move was asserted certain.
- `position`: JSON-encoded board view and mine layout at the moment of loss.

---

## 4. Loss Corpus & Deterministic Replay

When benchmarking with `--log-losses <path>`, every loss serializes:
- Board dimensions and difficulty level.
- Exact boolean mine layout matrix.
- Visible view state at death.
- The fatal move coordinate and certainty flag.

### Deterministic Replay
Because `Session.set_mine_layout(mines)` writes the recorded mine layout directly into `WINMINE.EXE` process memory, `src/tools/replay.py` can re-run those exact positions:
- Confirms the game reproduces the loss.
- Verifies that new solver logic fixes previously failed deductions without regressions.
