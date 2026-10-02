#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from carat.public_inputs import verify_public_patch_snapshots, verify_pytest_pair


def main() -> int:
    errors = []
    errors.extend(verify_pytest_pair(ROOT / "inputs" / "public" / "pytest"))
    errors.extend(verify_public_patch_snapshots(ROOT / "inputs" / "public-patches"))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("verified retained pytest pair and public patch snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
