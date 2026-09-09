"""Discovery and loading of agents from the `agents/` directory.

The split mirrors the trust boundary: `src/minebencher/` is the privileged
harness that holds the process handle and the oracle, `agents/` is
contributor space where nothing has either.

Drop a `.py` file (or a package) into `agents/` and it is a candidate.
Discovery does not consult filenames or display names: it loads each
module, finds an `act` method, and identifies the agent by hashing its
source. Labels (`agent_id`, `version`) are optional and cosmetic.

Files and directories whose names start with `.` or `_` are skipped, so
templates and notes can live beside real agents without being run.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .agent import describe

_ROOT = Path(__file__).resolve().parent.parent.parent if Path(__file__).resolve().parent.parent.name == "src" else Path(__file__).resolve().parent.parent
AGENTS_DIR = _ROOT / "agents"


@dataclass(frozen=True)
class Registration:
    cls: type
    source: Path
    fingerprint: str
    agent_id: str
    version: str
    privileged: bool
    description: str
    baseline: str | None

    @property
    def label(self) -> str:
        return f"{self.agent_id}@{self.version}"


@dataclass
class Discovery:
    agents: list[Registration]
    errors: dict[str, str]        # module path -> error text
    skipped: dict[str, str]       # duplicate-hash files, not errors


def _candidate_modules(directory: Path) -> list[Path]:
    found = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith((".", "_")):
            continue
        if path.suffix == ".py":
            found.append(path)
        elif path.is_dir() and (path / "__init__.py").exists():
            found.append(path / "__init__.py")
    return found


def _load_module(path: Path):
    stem = path.parent.name if path.name == "__init__.py" else path.stem
    name = f"_mb_agents.{stem}_{id(path)}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name, module


def _agent_classes(module_name: str, module) -> list[type]:
    found = []
    for obj in vars(module).values():
        if not inspect.isclass(obj) or obj.__module__ != module_name:
            continue
        if not callable(getattr(obj, "act", None)):
            continue
        found.append(obj)
    return found


def _construct(cls: type, seed: Optional[int] = None):
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        params = {}
    if "seed" in params:
        return cls(seed=seed)
    return cls()


def instantiate(reg: Registration, seed: Optional[int] = None):
    """Construct an agent, passing `seed` only if it accepts one."""
    return _construct(reg.cls, seed)


def discover(directory: Path = AGENTS_DIR) -> Discovery:
    agents: list[Registration] = []
    errors: dict[str, str] = {}
    skipped: dict[str, str] = {}
    by_hash: dict[str, Path] = {}
    if not directory.exists():
        return Discovery(agents, errors, skipped)

    for path in _candidate_modules(directory):
        try:
            name, module = _load_module(path)
        except Exception as exc:                      # noqa: BLE001
            errors[str(path)] = f"{type(exc).__name__}: {exc}"
            continue

        classes = _agent_classes(name, module)
        if not classes:
            errors[str(path)] = "no class with an act() method"
            continue
        if len(classes) > 1:
            names = ", ".join(c.__name__ for c in classes)
            errors[str(path)] = (
                f"multiple act() classes ({names}); one agent per file, "
                "because identity is the file hash")
            continue

        cls = classes[0]
        try:
            info = describe(_construct(cls))
        except Exception as exc:                      # noqa: BLE001
            errors[str(path)] = f"{type(exc).__name__}: {exc}"
            continue

        if info.fingerprint in by_hash:
            skipped[str(path)] = (
                f"same source hash as {by_hash[info.fingerprint]} "
                f"[{info.fingerprint}]; not a separate player")
            continue
        by_hash[info.fingerprint] = path
        agents.append(Registration(
            cls=cls, source=path, fingerprint=info.fingerprint,
            agent_id=info.agent_id, version=info.version,
            privileged=info.privileged, description=info.description,
            baseline=info.baseline,
        ))
    return Discovery(agents, errors, skipped)


class IncompleteBaselines(RuntimeError):
    """An experiment is not valid without both a floor and a ceiling."""


def baselines(found: Discovery) -> tuple[Registration, Registration]:
    """Return (floor, ceiling). Refuse if either control is missing.

    Comparison is only meaningful when white-noise and the oracle ran in
    the same session, at the same N and levels, as every candidate.
    """
    floors = [a for a in found.agents if a.baseline == "floor"]
    ceilings = [a for a in found.agents if a.baseline == "ceiling"]
    problems = []
    if len(floors) != 1:
        problems.append(f"need exactly one floor baseline, found {len(floors)}")
    if len(ceilings) != 1:
        problems.append(
            f"need exactly one ceiling baseline (the oracle), found {len(ceilings)}")
    if problems:
        raise IncompleteBaselines("; ".join(problems))
    return floors[0], ceilings[0]


def roster(found: Discovery, extra: list[Registration] | None = None
           ) -> list[Registration]:
    """Floor, then candidates, then ceiling. Baselines are never omitted."""
    floor, ceiling = baselines(found)
    seen = {floor.fingerprint, ceiling.fingerprint}
    middle = []
    for a in (extra if extra is not None else found.agents):
        if a.fingerprint in seen:
            continue
        seen.add(a.fingerprint)
        middle.append(a)
    return [floor, *middle, ceiling]


def load(fingerprint: str, seed: Optional[int] = None,
         directory: Path = AGENTS_DIR):
    """Load by hash (or unique prefix). Names are not required."""
    found = discover(directory)
    hits = [r for r in found.agents if r.fingerprint.startswith(fingerprint)]
    if len(hits) != 1:
        known = ", ".join(r.fingerprint for r in found.agents) or "none"
        raise KeyError(f"{fingerprint!r} matched {len(hits)} agents; "
                       f"known hashes: {known}")
    return instantiate(hits[0], seed)
