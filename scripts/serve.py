#!/usr/bin/env python3
"""Run five local accounting endpoints. Stop with the normal process interrupt."""
import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from carat.service import serve


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(serve(args.directory, args.ready_file))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
