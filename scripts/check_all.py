#!/usr/bin/env python3
"""Run all deterministic tests in one worker and retain an actual outcome."""
from __future__ import annotations
import io
import json
from pathlib import Path
import resource
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
start_cpu, start_wall = time.process_time(), time.perf_counter()
stream = io.StringIO()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))

def iter_cases(value):
    for item in value:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item

cases = list(iter_cases(suite))
module_level_tests = sum(isinstance(case, unittest.FunctionTestCase) for case in cases)
result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
record = {
    "tests_run": result.testsRun,
    "module_level_tests_run": module_level_tests,
    "failures": len(result.failures),
    "errors": len(result.errors), "skipped": len(result.skipped),
    "successful": result.wasSuccessful(),
    "measurement": {
        "cpu_seconds": round(time.process_time()-start_cpu,6),
        "elapsed_seconds": round(time.perf_counter()-start_wall,6),
        "peak_worker_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,3),
        "inherited_cumulative_cpu_seconds": None},
    "output": stream.getvalue().replace(str(ROOT), "<artifact>")}
output = ROOT / "results/summary/unit_tests.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(record, indent=2)+"\n")
print(record["output"], file=sys.stderr)
print(json.dumps({key: value for key,value in record.items() if key != "output"}, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
