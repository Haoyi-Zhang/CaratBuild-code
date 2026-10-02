#!/usr/bin/env python3
"""Small deterministic build tasks used by the executable adapter experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from carat.decode import decode  # noqa: E402
from carat.independent_check import check  # noqa: E402
from carat.public_pair import extract_pair  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="task", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--input", type=Path, required=True)
    prepare.add_argument("--facts", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--facts", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)

    package = subparsers.add_parser("package")
    package.add_argument("--facts", type=Path, required=True)
    package.add_argument("--report", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--wrong-mass", action="store_true")

    test = subparsers.add_parser("test")
    test.add_argument("--package", type=Path, required=True)
    test.add_argument("--result", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.task == "prepare":
            source = json.loads(args.input.read_text(encoding="utf-8"))
            facts = extract_pair(source)
            write_json(args.facts, facts)
            return 0
        if args.task == "verify":
            facts = json.loads(args.facts.read_text(encoding="utf-8"))
            decoded = decode(facts)
            checked = check(facts)
            if not decoded.determined or not checked.accepted or decoded.gross_mass != 15:
                return 2
            write_json(args.report, {
                "checker_accepted": checked.accepted,
                "determined": decoded.determined,
                "gross_mass": decoded.gross_mass,
                "unique_units": decoded.unique_units,
                "facts": len(facts),
            })
            return 0
        if args.task == "package":
            facts = json.loads(args.facts.read_text(encoding="utf-8"))
            report = json.loads(args.report.read_text(encoding="utf-8"))
            units = sorted(
                (fact["unit"], fact["mass"])
                for fact in facts if fact["kind"] == "mint"
            )
            mass = int(report["gross_mass"])
            if args.wrong_mass:
                mass -= 1
            write_json(args.output, {
                "facts": len(facts),
                "gross_mass": mass,
                "units": units,
            })
            return 0
        if args.task == "test":
            package_value = json.loads(args.package.read_text(encoding="utf-8"))
            accepted = (
                package_value.get("gross_mass") == 15
                and package_value.get("facts") == 6
                and sum(item[1] for item in package_value.get("units", [])) == 15
            )
            write_json(args.result, {"accepted": accepted})
            return 0 if accepted else 3
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
