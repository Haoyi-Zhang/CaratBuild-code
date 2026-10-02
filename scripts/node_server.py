#!/usr/bin/env python3
"""Run one independent CARAT endpoint process for the retained experiment."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from carat.multiprocess_service import serve_one  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(serve_one(args.name, args.directory, args.ready_file, args.port))


if __name__ == "__main__":
    main()
