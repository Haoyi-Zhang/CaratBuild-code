"""Reproduce the six-size lossless storage comparison; no services are launched."""
import argparse
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from carat.generate import mixed_history, recoordinate
from carat.storage_comparison import compare_storage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profiles = json.loads((root / "external_inputs/public_history_slices.json").read_text())
    if isinstance(profiles, dict):
        profiles = profiles["profiles"]
    rows = []
    for count in (1, 5, 10, 20, 40, 80):
        scenarios = []
        for index in range(count):
            profile = profiles[index % len(profiles)]
            scenarios.append(mixed_history(int(profile["added_lines"]) + int(profile["removed_lines"]) + index % 7,
                                           int(profile["files_changed"]), history_index=10000 + index,
                                           rewrite_rounds=2))
        facts = recoordinate(fact for scenario in scenarios for fact in scenario.facts)
        rows.append(compare_storage(facts))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"environment": {"python": sys.version, "platform": platform.platform()},
                                      "rows": rows}, indent=2) + "\n")
    print(json.dumps(rows[-1]))


if __name__ == "__main__":
    main()
