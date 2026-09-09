# Minebencher

A high-throughput, memory-synchronized self-play benchmarking harness and cumulative ranking system for Windows XP Minesweeper (`bin/WINMINE.EXE` 5.1.2600.0).

---

## Architecture Overview

```
+-------------------------------------------------------------------------+
|                              bin/WINMINE.EXE                            |
|                     (XP SP0 build 5.1.2600.0 x86)                       |
+--------------------+-------------------------------+--------------------+
                     ^                               |
       SendMessageW  |                               | ReadProcessMemory
       (WM_COMMAND,  |                               | (.data: 0x01005000)
        WM_L/RBUTTON)|                               v
+--------------------+----------------------------------------------------+
|                         Privileged Harness                              |
|                          (src/minebencher/)                             |
|                                                                         |
|   - Synchronous message-loop dispatch via SendMessageTimeoutW          |
|   - Single-pass atomic .data snapshotting & honest observation masking  |
|   - Dedicated Winmine process lifecycle and PID-bound window control    |
|   - Durable results persistence in SQLite (benchmarks/results_cumulative.db) |
|   - Automatic per-run and cumulative benchmarks in benchmarks/          |
+------------------------------------+------------------------------------+
                                     |
                    JSON-over-stdio  | (Observation serialised,
                              IPC    |  mines stripped)
                                     v
+-------------------------------------------------------------------------+
|                  Isolated Agent Processes                               |
|                      (agents/ → minebencher.worker)                    |
|                                                                         |
|   - Each agent runs in a dedicated child Python process                |
|   - Autonomous deductive solvers (single-point, subsets, CSP)           |
|   - Mandatory baselines (agents/random.py floor, agents/oracle.py ceil) |
|   - Content-addressed SHA-256 fingerprint identity                      |
+-------------------------------------------------------------------------+
```

---

## Repository Structure

```
.
├── .gitignore              # Ignores databases, candidate agents, and benchmarks/*
├── LICENSE                 # MIT License (Copyright 2026 David Carey)
├── README.md               # Project overview and quick start
├── AGENTS.md               # Solver authoring guide
│
├── bin/                    # Executable directory (.gitkeep tracked; user provides WINMINE.EXE)
│
├── benchmarks/             # Benchmarks directory (.gitkeep tracked, runs gitignored)
│   ├── report_cumulative.txt   # Cumulative leaderboard report across all runs
│   ├── results_cumulative.db   # Cumulative SQLite database (union of all runs)
│   └── <experiment_id>/        # Per-experiment isolated run archive
│       ├── report_<id>.txt     # Run report
│       └── results_<id>.db     # Run SQLite database
│
├── agents/                 # Autonomous solvers (content-addressed by SHA-256)
│   ├── _template.py        # Solver starter template (skipped by discovery)
│   ├── oracle.py           # Privileged ceiling baseline (knows mine layout)
│   ├── random.py           # White-noise floor baseline (uniform random clicks)
│   └── deductive.py        # Constraint solver with subset elimination (gitignored)
│
├── docs/                   # Detailed documentation
│   ├── architecture.md     # Memory layout, addresses, and Win32 synchronization
│   ├── protocol.md         # Formal Agent protocol & Observation/Move API reference
│   ├── benchmarking.md     # Wilson score 95% CIs, baselines, and database schema
│   └── tools.md            # CLI tools reference & options
│
└── src/
    ├── minebencher/        # Privileged harness, reader, input driver & store
    │   ├── agent_process.py    # Parent-side proxy; spawns & manages agent workers
    │   └── worker.py           # Child-process endpoint for isolated agent execution
    └── tools/              # CLI tools (bench.py, report.py, agents.py, etc.)
```

---

## Quick Start

### 1. Discover Solvers
Verify agent discovery and view source fingerprint hashes:

```bash
python src/tools/agents.py
```

### 2. Run a Benchmark
Run 100 games on Beginner difficulty. The harness automatically executes the floor, candidate solvers, and ceiling under identical conditions, saving isolated run artifacts to `benchmarks/<id>/` and updating `benchmarks/results_cumulative.db`:

```bash
python src/tools/bench.py --games 100 --level beginner
```

To run across all difficulty levels (`beginner`, `intermediate`, `expert`):
```bash
python src/tools/bench.py --games 100 --all-levels
```

### 3. View Leaderboard & Reports
Display statistical rankings with 95% Wilson confidence intervals. Reports are automatically saved to `benchmarks/`:

```bash
python src/tools/report.py
```

---

## Documentation Index

- **[AGENTS.md](AGENTS.md)**: Solver authoring quickstart and starter template.
- **[docs/protocol.md](docs/protocol.md)**: Full Agent protocol, `Observation` methods, `Move` structure, and soundness guarantees.
- **[docs/architecture.md](docs/architecture.md)**: Reverse-engineered memory layout, cell bitmasks, and Win32 input synchronization.
- **[docs/benchmarking.md](docs/benchmarking.md)**: Statistical evaluation, Wilson score intervals, and database schema.
- **[docs/tools.md](docs/tools.md)**: Complete command-line reference for all utilities in `src/tools/`.

---

## Requirements & Setup

1. **Operating System**: Windows (32-bit or 64-bit; standard Win32 subsystem APIs).
2. **Python**: Python 3.9+ (Standard library only; no pip packages needed for core benchmarking).
3. **Minesweeper Binary (`WINMINE.EXE`)**:
   Place an authentic Windows XP `WINMINE.EXE` binary into the `bin/` folder:
   - **Target Build**: Windows XP (build 5.1.2600.0, 32-bit x86, 119,808 bytes)
   - **SHA-256**: `bcff89311d792f6428468e813ac6929a346a979f907071c302f418d128eaaf41`
   - **How to Acquire**:
     - From a Windows XP installation or VM: copy `C:\WINDOWS\system32\winmine.exe` to `bin/WINMINE.EXE`.
     - From Windows XP installation media: expand `I386\WINMINE.EX_` using `expand -r WINMINE.EX_`.

---

## License

This project is released under the [MIT License](LICENSE).
Copyright (c) 2026 DRIFTER INDUSTRIAL.
