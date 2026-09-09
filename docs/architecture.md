# Minebencher Architecture & Memory Layout

This document details the reverse engineering, Win32 memory synchronization, and execution model used by Minebencher to benchmark Windows XP `WINMINE.EXE`.

---

## 1. Process & Memory Model

Minebencher drives genuine 32-bit Windows XP Minesweeper (`bin/WINMINE.EXE` build 5.1.2600.0). The binary has base relocations stripped and lacks ASLR (`DYNAMICBASE`), loading at preferred base `0x01000000`. The reader dynamically verifies and resolves module base offsets at runtime regardless.

### Target Binary Specification & Acquisition
To run benchmarks, place an authentic copy of `WINMINE.EXE` into `bin/`:
- **Build**: Windows XP SP0–SP3 (32-bit x86, build `5.1.2600.0`, `119,808` bytes)
- **SHA-256**: `bcff89311d792f6428468e813ac6929a346a979f907071c302f418d128eaaf41`
- **Acquisition**:
  - From a Windows XP machine or VM: `C:\WINDOWS\system32\winmine.exe`.
  - From Windows XP installation media: extract `I386\WINMINE.EX_` via `expand -r WINMINE.EX_`.

### Coherent Single-Pass Snapshotting
A critical issue in process memory reading is torn reads (reading state while the game thread is mid-update). Minebencher pulls the entire `.data` section in a single `ReadProcessMemory` syscall:

- **Section Base**: `0x01005000`
- **Section Size**: `0x00000B98` bytes

All board cells, status flags, and counters are extracted from this single atomic snapshot, guaranteeing an internally consistent observation.

---

## 2. Reverse-Engineered Globals

Derived via disassembly and static xref mapping (`src/tools/static_map.py`, `src/tools/disasm.py`):

| Address | Type | Description |
| :--- | :--- | :--- |
| `0x01005000` | `DWORD` | Game flags bitfield (`0x01` active, `0x02` paused, `0x10` over). |
| `0x01005160` | `DWORD` | Status word (`0` in play, `2` lost, `3` won). |
| `0x01005194` | `DWORD` | Mine counter shown in UI (`mine_total - flags_placed`). |
| `0x01005330` | `DWORD` | Total mines placed on the board. |
| `0x01005334` | `DWORD` | Width in columns ($W \le 30$). |
| `0x01005338` | `DWORD` | Height in rows ($H \le 24$). |
| `0x01005340` | `BYTE[27][32]` | Board array (1-indexed, 32-byte stride, `0x10` border). |
| `0x010056A0` | `WORD` | Difficulty level (`0` beginner, `1` intermediate, `2` expert, `3` custom). |
| `0x010056BC` | `DWORD` | Question marks enabled flag. |
| `0x010056CC` | `DWORD[3]` | High score best times for beginner, intermediate, and expert. |
| `0x0100579C` | `DWORD` | Elapsed game time in seconds. |
| `0x010057A0` | `DWORD` | Target safe cells to uncover ($W \times H - \text{mines}$). |
| `0x010057A4` | `DWORD` | Count of cells uncovered so far. |

---

## 3. Board Array & Cell Byte Encoding

The board is laid out as a flat memory array of 27 rows with a fixed **32-byte stride**:
- Coordinate `(x, y)` maps to offset: `(y + 1) * 32 + (x + 1)`.
- Row `0`, row `H + 1`, column `0`, and column `W + 1` contain `0x10` border sentinels.

### Bitmask Definitions

```text
+---+---+---+---+---+---+---+---+
| 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 |
+---+---+---+---+---+---+---+---+
  |   |   \___________________/
  |   |              |
  |   |              +-- Value Mask (0x1F): cell state or count
  |   +----------------- Open Bit (0x40): cell uncovered
  +--------------------- Mine Bit (0x80): mine present
```

- **Covered Cell**: `0x0F` (with `0x80` if a mine is underneath).
- **Flagged Cell**: `0x0E`.
- **Question Mark**: `0x0D`.
- **Border Sentinel**: `0x10`.
- **Uncovered Cell**: `0x40 | count` where `count` is `0..8`.
- **Loss Post-Mortem Markers**:
  - `0xCC`: Exploded mine stepped on by the player (`SetCell` stamped `0x4C` OR `0x80`).
  - `0x8A`: Unrevealed mine exposed upon loss.
  - `0x0B`: Misplaced flag (flagged cell that was not a mine).

---

## 4. Win32 Input Synchronization

Standard UI automation using `PostMessage` + `sleep()` introduces race conditions where messages are processed late, or queue barriers overtake asynchronous inputs.

Minebencher uses **synchronous dispatch**:
1. Mouse clicks are dispatched via `SendMessageTimeoutW`.
2. Cross-thread `SendMessage` forces Windows to invoke the target window's `WindowProc` synchronously on the receiving thread and blocks until the window procedure returns.
3. Memory reads executed immediately after the function returns are guaranteed to reflect the state transitions triggered by the click.

### Runtime Coordinate Calibration
The physical grid origin in screen client pixels is derived at runtime via `calibrate()`:
- The harness right-clicks a test cell.
- Reads process memory to determine which cell was toggled.
- Calibrates exact pixel coordinates `(ox, oy)` and cell width (`16px`) without hardcoded visual assumptions.

---

## 5. Harness Guardrails & Board Injection

- **Dialog Suppression**: Winning a game faster than the current best time triggers a modal "New High Score" dialog that blocks the message pump. Minebencher zeroes the high score entries at `0x010056CC`; since elapsed seconds cannot be negative, the dialog never triggers.
- **Marks Disabled**: Clears `0x010056BC` so right-clicks toggle cleanly between covered and flagged without cycling through question marks.
- **Owned Process**: A default session launches and later terminates its own `WINMINE.EXE` process instead of attaching to an unrelated open game.
- **PID-Bound Input**: Window discovery verifies the owning process ID, so clicks and memory snapshots always target the same Winmine instance.
- **Process Mutex**: Each session acquires a named Windows mutex `Local\winmine-harness-{pid}`. If two scripts attempt to drive the same PID simultaneously, the second safely errors out.

---

## 6. Agent Subprocess Isolation

Agent code is never imported into the harness process. Each agent runs in a dedicated child Python process (`minebencher.worker`), communicating via a line-delimited JSON protocol over stdin/stdout.

### Worker Lifecycle

```
Harness (parent)                         Worker (child)
    |                                        |
    |--- subprocess.Popen ------------------>|
    |                                        |-- importlib loads agent module
    |<-- {"type":"ready", ...} --------------|  (print() redirected to stderr)
    |                                        |
    |--- {"type":"act", "observation":{...}}->|
    |                                        |-- agent.act(obs)
    |<-- {"type":"moves", "moves":[...]} ----|
    |        ... (repeats per turn) ...      |
    |                                        |
    |--- {"type":"close"} ------------------>|
    |                                        |-- exit(0)
```

### Fault Isolation
- A crashing agent sends `{"type":"error", ...}` and exits; the harness logs the failure without itself dying.
- A hanging agent is killed after the remaining game-time deadline (999 − elapsed seconds) plus a startup grace period of 10 seconds.
- Agent `print()` calls are redirected to stderr inside the worker so they cannot corrupt the JSON protocol stream.

### Discovery
Registry discovery (`probe_agent`) spawns a short-lived worker per candidate file, reads the `ready` message for metadata (agent_id, version, fingerprint, baseline), and immediately closes the process. No agent code is ever evaluated in the parent.
