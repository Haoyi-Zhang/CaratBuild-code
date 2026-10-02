#!/usr/bin/env python
"""Stage runner and result collector for the CARAT experiments."""

from __future__ import annotations

import argparse
import json
import os
import signal
import resource
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from carat.experiments import (
    load_profiles,
    run_ambiguity,
    run_compaction,
    run_exhaustive,
    run_faults,
    run_manipulation,
    run_scale,
)

STAGES = ("manipulation", "faults", "ambiguity", "scale", "compaction", "exhaustive")
STAGE_TIMEOUT_SECONDS = 180


def _close(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return abs(left - right) <= tolerance


def validate_semantics(result: dict[str, object]) -> None:
    """Reject partial or scientifically failed campaign summaries."""

    manipulation = result["manipulation"]
    faults = result["faults"]
    ambiguity = result["ambiguity"]
    scale = result["scale"]
    compaction = result["compaction"]
    exhaustive = result["exhaustive"]
    sections = {
        "manipulation": manipulation,
        "faults": faults,
        "ambiguity": ambiguity,
        "scale": scale,
        "compaction": compaction,
        "exhaustive": exhaustive,
    }
    malformed = [name for name, value in sections.items() if not isinstance(value, dict)]
    if malformed:
        raise AssertionError("malformed campaign summaries: " + ", ".join(malformed))

    failures: list[str] = []
    if manipulation.get("runs") != 720 or manipulation.get("maximum_copies") != 16:
        failures.append("manipulation campaign count")
    for key, expected in (
        ("maximum_carat_ratio", 1.0),
        ("maximum_count_ratio", 17.0),
        ("maximum_presentation_mass_ratio", 17.0),
        ("fork_multi_output_runs", 96),
        ("squash_multi_input_runs", 120),
    ):
        value = manipulation.get(key)
        if not isinstance(value, (int, float)) or not _close(float(value), expected):
            failures.append(f"manipulation {key}")

    if any(
        faults.get(key) != expected
        for key, expected in (
            ("runs", 160),
            ("converged", 160),
            ("determined", 160),
            ("checker_accepted", 160),
            ("maximum_mass_error", 0),
            ("arrival_order_branch_divergent_runs", 141),
        )
    ):
        failures.append("fault campaign semantics")

    if any(
        ambiguity.get(key) != expected
        for key, expected in (
            ("runs", 400),
            ("detected", 400),
            ("verified", 400),
            ("maximum_witness_size", 3),
        )
    ):
        failures.append("ambiguity campaign semantics")

    if scale.get("largest_units") != 20000 or scale.get("largest_facts") != 20013:
        failures.append("scale campaign bound")

    reduction = compaction.get("largest_reduction_fraction")
    if (
        compaction.get("largest_batches") != 80
        or compaction.get("largest_facts") != 3025
        or compaction.get("all_accounting_equal") is not True
        or not isinstance(reduction, (int, float))
        or not _close(float(reduction), 0.692665)
    ):
        failures.append("compaction campaign semantics")

    if any(
        exhaustive.get(key) != expected
        for key, expected in (
            ("delivery_cases", 960),
            ("delivery_failures", 0),
            ("operation_sequences", 39),
            ("operation_failures", 0),
            ("gap_hole_counterexamples", 1),
            ("gap_hole_starved_facts", 6),
        )
    ):
        failures.append("exhaustive campaign semantics")

    if failures:
        raise AssertionError("reproduction validation failed: " + "; ".join(failures))


def _stage_timeout(_signum: int, _frame: object) -> None:
    raise TimeoutError(f"experiment stage exceeded {STAGE_TIMEOUT_SECONDS} seconds")


def run_stage(name: str) -> dict[str, object]:
    raw = ROOT / "results" / "raw"
    summary = ROOT / "results" / "summary"
    profiles = load_profiles(ROOT / "external_inputs" / "public_history_slices.json")
    if name == "manipulation":
        return run_manipulation(profiles, raw, summary)
    if name == "faults":
        return run_faults(profiles, raw, summary)
    if name == "ambiguity":
        return run_ambiguity(profiles, raw, summary)
    if name == "scale":
        return run_scale(raw, summary)
    if name == "compaction":
        return run_compaction(profiles, raw, summary)
    if name == "exhaustive":
        return run_exhaustive(raw, summary)
    raise ValueError(f"unknown stage: {name}")


def collect(elapsed_seconds: float) -> dict[str, object]:
    summary = ROOT / "results" / "summary"
    result: dict[str, object] = {}
    for stage in STAGES:
        path = summary / f"{stage}_overview.json"
        result[stage] = json.loads(path.read_text(encoding="utf-8"))
    validate_semantics(result)
    result["elapsed_seconds"] = round(elapsed_seconds, 3)
    (summary / "overview.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--elapsed", type=float, default=0.0)
    args = parser.parse_args()

    if args.stage:
        signal.signal(signal.SIGALRM, _stage_timeout)
        signal.alarm(STAGE_TIMEOUT_SECONDS)
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            output = run_stage(args.stage)
        finally:
            signal.alarm(0)
        output["measurement"] = {
            "cpu_seconds": round(time.process_time()-cpu,6),
            "elapsed_seconds": round(time.perf_counter()-wall,6),
            "peak_worker_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3),
            "rss_scope": "single worker high-water mark including driver; not live-state allocation",
            "inherited_cumulative_cpu_seconds": None}
        summary = ROOT / "results" / "summary"
        summary.mkdir(parents=True, exist_ok=True)
        (summary / f"{args.stage}_overview.json").write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    if args.collect:
        print(json.dumps(collect(args.elapsed), indent=2, sort_keys=True))
        return 0

    os.environ["CARAT_PYTHON"] = sys.executable
    os.execv("/bin/bash", ["bash", str(ROOT / "scripts" / "reproduce.sh")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
