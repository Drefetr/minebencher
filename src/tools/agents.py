"""List every agent discovered by loading `agents/`.

    python tools/agents.py
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from minebencher.registry import AGENTS_DIR, discover


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=AGENTS_DIR)
    args = ap.parse_args()

    found = discover(args.dir)
    print(f"agents directory: {args.dir}\n")

    if not found.agents:
        print("  none discovered")
    else:
        print(f"  {'hash':<12}  {'role':<9} {'label':<16} {'source':<22} desc")
        print("  " + "-" * 92)
        for reg in found.agents:
            role = (reg.baseline or "candidate")
            rel = reg.source.relative_to(ROOT)
            print(f"  {reg.fingerprint:<12}  {role:<9} {reg.label:<16} "
                  f"{str(rel):<22} {reg.description}")

    if found.skipped:
        print("\n  skipped (duplicate hash):")
        for path, why in found.skipped.items():
            print(f"    {path}: {why}")
    if found.errors:
        print("\n  failed to load:")
        for path, err in found.errors.items():
            print(f"    {path}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
