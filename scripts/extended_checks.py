#!/usr/bin/env python3
"""Run one capped boundary experiment, recording actual resource consumption."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import resource
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from carat.extended_experiments import run

STAGES = ("identity", "transport", "service-pilot", "service", "recovery", "compaction-boundaries", "public-pair", "semantic-boundaries", "sealed-windows", "multiprocess-retention", "completion-boundaries", "executable-build")

def timeout(_signal: int, _frame: object) -> None:
    raise TimeoutError("boundary stage exceeded 180 seconds")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=STAGES)
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(180)
    before = time.process_time()
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall = time.perf_counter()
    failure = None
    try:
        result = run(ROOT, args.stage)
    except Exception as exc:
        failure = exc
        result = {"completed": False, "error_type": type(exc).__name__, "error": str(exc)[:300]}
    finally:
        signal.alarm(0)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result["measurement"] = {
        "elapsed_seconds": round(time.perf_counter()-wall, 6),
        "cpu_seconds": round(time.process_time()-before+children.ru_utime+children.ru_stime-
                             children_before.ru_utime-children_before.ru_stime, 6),
        "peak_worker_rss_mib": round(max(usage.ru_maxrss, children.ru_maxrss)/1024, 3),
        "rss_scope": "largest reported worker high-water mark, not concurrent aggregate RSS",
        "inherited_cumulative_cpu_seconds": None}
    directory = ROOT/"results/summary"
    directory.mkdir(parents=True, exist_ok=True)
    (directory/(args.stage.replace("-", "_")+"_boundary_overview.json")).write_text(
        json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if failure is None else 1

if __name__ == "__main__":
    raise SystemExit(main())
