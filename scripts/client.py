#!/usr/bin/env python3
"""Send one JSON request from standard input to a local CARAT endpoint."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from carat.service import rpc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoints", type=Path, required=True)
    parser.add_argument("--node", choices=[f"n{i}" for i in range(5)], required=True)
    args = parser.parse_args()
    endpoints = json.loads(args.endpoints.read_text())
    request = json.load(sys.stdin)
    result, _bytes = asyncio.run(rpc(endpoints[args.node], request))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
