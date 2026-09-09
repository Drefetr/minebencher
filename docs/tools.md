# Minebencher CLI Tools Reference

This document provides command-line syntax and option details for all utilities in `src/tools/`.

---

## 1. Benchmarking & Evaluation

### `src/tools/bench.py`
Runs controlled experiments comparing candidate solvers against baseline controls under identical conditions:

```bash
python src/tools/bench.py [options]
```

#### Key Arguments
- `--games <N>`: Number of games per level (default: `100`).
- `--level <LVL>`: Difficulty level: `beginner`, `intermediate`, or `expert` (default: `beginner`).
- `--all-levels`: Run across all three standard difficulty levels sequentially.
- `--only <HASH_OR_NAME>`: Filter candidate solvers by SHA-256 prefix or `agent_id` (baselines still run).
- `--seed <S>`: PRNG seed passed to agents supporting seeding (default: `0`).
- `--db <PATH>`: Cumulative database path (default: `benchmarks/results_cumulative.db`).
- `--no-record`: Run benchmark without persisting results to SQLite.
- `--log-losses <PATH>`: Output JSONL log of loss positions for regression replay.
- `--progress-every <N>`: Print progress update every $N$ games.

Outputs are archived to:
- `benchmarks/<id>/results_<id>.db` (isolated run database)
- `benchmarks/<id>/report_<id>.txt` (isolated run report)
- `benchmarks/results_cumulative.db` (cumulative union database)
- `benchmarks/report_cumulative.txt` (cumulative report)

---

### `src/tools/report.py`
Renders formatted leaderboard tables with 95% Wilson score confidence intervals and automatically saves reports to `benchmarks/`:

```bash
python src/tools/report.py [options]
```

#### Key Arguments
- `--experiment <ID>`: Render results for a specific experiment ID (saved to `benchmarks/<id>/report_<id>.txt`).
- `--pooled`: Aggregate all recorded runs across sessions (saved to `benchmarks/report_cumulative.txt`).
- `--history <KEY>`: Show historical progression for an agent fingerprint or display label.
- `--level <LVL>`: Filter output to a specific level (`beginner`, `intermediate`, `expert`).
- `--save` / `--no-save`: Toggle saving report output to disk (default: `--save`).
- `--out <PATH>`: Custom output filepath for the text report.
- `--db <PATH>`: Database path (default: `benchmarks/results_cumulative.db`).

---

### `src/tools/agents.py`
Scans the `agents/` directory, compiles hashes, validates classes, and lists all discovered agents:

```bash
python src/tools/agents.py [--dir <PATH>]
```

---

## 2. Regression & Diagnostics

### `src/tools/replay.py`
Injects recorded loss positions into `WINMINE.EXE` memory to test determinism:

```bash
python src/tools/replay.py <loss_corpus.jsonl> [--limit <N>]
```

### `src/tools/verify.py`
Validates memory layout and internal consistency checks against an active game:

```bash
python src/tools/verify.py
```

### `src/tools/prune.py`
Inspects identities and deletes specific agent runs from `results.db`:

```bash
python src/tools/prune.py                                 # List all identities
python src/tools/prune.py --fingerprint <HASH> --dry-run  # Preview deletion
python src/tools/prune.py --fingerprint <HASH>            # Delete identity runs
```

---

## 3. Internal Development & Reverse Engineering (Gitignored)

The following scripts served as reverse-engineering and initial verification scaffolding during development and are gitignored in distribution:

- **`src/tools/disasm.py`**: Recursive-descent disassembler for `bin/WINMINE.EXE` (requires `capstone`).
- **`src/tools/static_map.py`**: Scans `.text` for memory references into `.data` (requires `capstone`).
- **`src/tools/xref.py`**: Assembly cross-reference inspector for memory addresses (requires `capstone`).
- **`src/tools/exercise.py`**: Interactive calibration and blind click smoke test.
- **`src/tools/edge_cases.py`**: Diagnostic probe for Expert 30-wide grid strides and deliberate loss markers.
