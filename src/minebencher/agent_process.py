"""Parent-side proxy for an agent running in a dedicated Python process."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Optional

from .agent import AgentInfo, Move, Observation

_ROOT = Path(__file__).resolve().parent.parent.parent
_GAME_TIME_LIMIT = 999
_STARTUP_TIMEOUT = 10.0


class AgentProcessError(RuntimeError):
    """The isolated agent failed to start, execute, or speak the protocol."""


class AgentTimedOut(TimeoutError):
    """The isolated agent exceeded the remaining game time."""


class AgentProcess:
    """An Agent-compatible JSON proxy backed by a child process."""

    def __init__(self, source: Path, seed: Optional[int] = None,
                 expected_fingerprint: Optional[str] = None):
        self.source = Path(source).resolve()
        self.seed = seed
        self._responses: queue.Queue[Optional[str]] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=40)
        self._closed = False
        self._timed_out = False
        self._game_time_limit = _GAME_TIME_LIMIT

        env = os.environ.copy()
        paths = [str(_ROOT / "src"), str(_ROOT)]
        if env.get("PYTHONPATH"):
            paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(paths)
        command = [sys.executable, "-m", "minebencher.worker", str(self.source)]
        if seed is not None:
            command += ["--seed", str(seed)]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            command, cwd=str(_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, creationflags=creationflags,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        try:
            ready = self._receive(_STARTUP_TIMEOUT)
            if ready.get("type") != "ready":
                raise AgentProcessError(
                    f"agent worker returned {ready.get('type')!r} during startup")
            if (expected_fingerprint is not None and
                    ready["fingerprint"] != expected_fingerprint):
                raise AgentProcessError(
                    "agent source changed between discovery and execution")
            self.agent_id = ready["agent_id"]
            self.version = ready["version"]
            self.fingerprint = ready["fingerprint"]
            self.privileged = bool(ready["privileged"])
            self.description = ready["description"]
            self.baseline = ready["baseline"]
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())

    def _detail(self) -> str:
        return "\n".join(self._stderr) or "no worker diagnostics"

    def _receive(self, timeout: float) -> dict:
        try:
            line = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            self._timed_out = True
            self._terminate()
            raise AgentTimedOut(
                f"agent {self.source} exceeded its {timeout:.1f}s deadline") from exc
        if line is None:
            code = self._process.poll()
            raise AgentProcessError(
                f"agent worker exited with code {code}: {self._detail()}")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentProcessError(
                f"invalid agent protocol response {line!r}: {self._detail()}") from exc
        if response.get("type") == "error":
            raise AgentProcessError(
                f"{response.get('error', 'agent failed')}: {self._detail()}")
        return response

    def _send(self, payload: dict) -> None:
        if self._timed_out:
            raise AgentTimedOut(f"agent {self.source} previously timed out")
        if self._closed or self._process.poll() is not None:
            raise AgentProcessError("agent worker is not running")
        assert self._process.stdin is not None
        self._process.stdin.write(
            json.dumps(payload, separators=(",", ":")) + "\n")
        self._process.stdin.flush()

    def act(self, obs: Observation,
            mines: Optional[list[list[bool]]] = None) -> list[Move]:
        payload = {
            "type": "act",
            "observation": {
                "width": obs.width, "height": obs.height,
                "mine_total": obs.mine_total, "mines_left": obs.mines_left,
                "elapsed": obs.elapsed, "opened": obs.opened,
                "safe_cells": obs.safe_cells, "view": obs.view,
            },
        }
        if self.privileged:
            payload["mines"] = mines
        self._send(payload)
        remaining = max(0.1, self._game_time_limit - obs.elapsed)
        response = self._receive(remaining)
        if response.get("type") != "moves":
            raise AgentProcessError(
                f"unexpected agent response {response.get('type')!r}")

        moves = []
        for item in response.get("moves", []):
            if not isinstance(item, list) or len(item) != 4:
                moves.append(Move("__invalid__", 0, 0, False))
                continue
            moves.append(Move(item[0], item[1], item[2], item[3]))
        return moves

    def set_time_limit(self, seconds: int) -> None:
        self._game_time_limit = seconds

    def _terminate(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.poll() is None and self._process.stdin is not None:
            try:
                self._process.stdin.write('{"type":"close"}\n')
                self._process.stdin.flush()
                self._process.wait(timeout=1)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self._terminate()
        for stream in (self._process.stdin, self._process.stdout,
                       self._process.stderr):
            if stream is not None:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def probe_agent(source: Path) -> AgentInfo:
    """Load and describe an agent without importing it into this process."""
    with AgentProcess(source) as agent:
        return AgentInfo(
            agent_id=agent.agent_id,
            version=agent.version,
            fingerprint=agent.fingerprint,
            privileged=agent.privileged,
            description=agent.description,
            baseline=agent.baseline,
        )
