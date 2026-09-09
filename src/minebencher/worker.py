"""Child-process endpoint for isolated agent discovery and execution."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import inspect
import json
import sys
import traceback
from pathlib import Path

from .agent import Move, Observation, describe


def _load_agent(path: Path, seed: int | None):
    stem = path.parent.name if path.name == "__init__.py" else path.stem
    module_name = f"_mb_worker_agent_{stem}_{id(path)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    classes = [
        obj for obj in vars(module).values()
        if inspect.isclass(obj) and obj.__module__ == module_name
        and callable(getattr(obj, "act", None))
    ]
    if not classes:
        raise TypeError("no class with an act() method")
    if len(classes) > 1:
        names = ", ".join(cls.__name__ for cls in classes)
        raise TypeError(f"multiple act() classes ({names}); one agent per file")

    cls = classes[0]
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        params = {}
    return cls(seed=seed) if "seed" in params else cls()


def _observation(data: dict) -> Observation:
    data = dict(data)
    data["view"] = tuple(tuple(row) for row in data["view"])
    return Observation(**data)


def _moves(decided) -> list[list]:
    encoded = []
    for move in decided:
        if not isinstance(move, Move):
            encoded.append(["__invalid__", 0, 0, False])
            continue
        encoded.append([move.action, move.x, move.y, move.certain])
    return encoded


def _write(stream, payload: dict) -> None:
    stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    protocol_out = sys.stdout

    try:
        # Ordinary print() calls from agent imports and actions must not enter
        # the JSON protocol stream.
        with contextlib.redirect_stdout(sys.stderr):
            agent = _load_agent(args.path.resolve(), args.seed)
            info = describe(agent)
        _write(protocol_out, {
            "type": "ready",
            "agent_id": info.agent_id,
            "version": info.version,
            "fingerprint": info.fingerprint,
            "privileged": info.privileged,
            "description": info.description,
            "baseline": info.baseline,
        })

        for line in sys.stdin:
            request = json.loads(line)
            if request.get("type") == "close":
                return 0
            if request.get("type") != "act":
                raise ValueError(f"unknown request type {request.get('type')!r}")

            obs = _observation(request["observation"])
            with contextlib.redirect_stdout(sys.stderr):
                if info.privileged:
                    decided = agent.act(obs, request.get("mines"))
                else:
                    decided = agent.act(obs)
                moves = _moves(decided)
            _write(protocol_out, {"type": "moves", "moves": moves})
    except Exception as exc:  # noqa: BLE001 - errors must cross the boundary
        traceback.print_exc(file=sys.stderr)
        try:
            _write(protocol_out, {
                "type": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
