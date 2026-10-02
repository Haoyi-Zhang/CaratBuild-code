#!/usr/bin/env python3
"""Run the complete deterministic artifact in process-isolated stages.

Each campaign gets a fresh interpreter.  This keeps event loops, signal state,
open sockets, and child-process bookkeeping from leaking across stages while
preserving the one-command reproduction surface.
"""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results" / "summary"
RAW = ROOT / "results" / "raw"
TIMEOUT_SECONDS = 185

ORIGINAL_STAGES = (
    "manipulation",
    "faults",
    "ambiguity",
    "scale",
    "compaction",
    "exhaustive",
)
BOUNDARY_STAGES = (
    "identity",
    "transport",
    "service-pilot",
    "service",
    "recovery",
    "compaction-boundaries",
    "public-pair",
    "semantic-boundaries",
    "sealed-windows",
    "multiprocess-retention",
    "completion-boundaries",
    "executable-build",
)


def run_checked(arguments: list[str], label: str, *, show_output: bool = False) -> str:
    print(f"running {label}", file=sys.stderr, flush=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Real files avoid PIPE EOF deadlocks if a stage briefly spawns a descendant
    # that inherits the standard streams.  We still retain diagnostics on failure.
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as out, \
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as err:
        try:
            completed = subprocess.run(
                arguments,
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=out,
                stderr=err,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"{label} exceeded {TIMEOUT_SECONDS} seconds") from exc
        out.seek(0)
        stdout = out.read()
        err.seek(0)
        stderr = err.read()
    if completed.returncode != 0:
        if stdout:
            print(stdout, file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(completed.returncode, arguments)
    if show_output:
        if stderr:
            print(stderr, file=sys.stderr, end="")
        if stdout:
            print(stdout, end="")
    return stdout


def _run_locked() -> int:
    SUMMARY.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    for name in (
        "complete_overview.json",
        "artifact_audit.json",
        "overview.json",
    ):
        (SUMMARY / name).unlink(missing_ok=True)
    for stage in ORIGINAL_STAGES:
        (SUMMARY / f"{stage}_overview.json").unlink(missing_ok=True)
    for stage in BOUNDARY_STAGES:
        (SUMMARY / f"{stage.replace('-', '_')}_boundary_overview.json").unlink(missing_ok=True)

    started = time.perf_counter()
    run_checked([sys.executable, str(ROOT / "scripts/check_all.py")], "tests")
    for stage in ORIGINAL_STAGES:
        run_checked(
            [sys.executable, str(ROOT / "scripts/reproduce.py"), "--stage", stage],
            stage,
        )
    elapsed = time.perf_counter() - started
    run_checked(
        [
            sys.executable,
            str(ROOT / "scripts/reproduce.py"),
            "--collect",
            "--elapsed",
            f"{elapsed:.6f}",
        ],
        "original collector",
    )
    for stage in BOUNDARY_STAGES:
        run_checked(
            [sys.executable, str(ROOT / "scripts/extended_checks.py"), "--stage", stage],
            stage,
        )

    collector = run_checked(
        [sys.executable, str(ROOT / "scripts/collect_all.py")],
        "complete collector",
    )
    audit = run_checked(
        [sys.executable, str(ROOT / "scripts/audit_artifact.py")],
        "artifact audit",
    )
    # Emit compact, machine-readable closure summaries instead of all per-stage JSON.
    print(collector.strip())
    print(audit.strip())
    return 0


def main() -> int:
    # The driver rewrites one deterministic result tree.  Two concurrent runs
    # in the same extraction would otherwise delete and recreate each other's
    # stage summaries, producing a misleading partial failure.  Lock the driver
    # source itself so the guard creates no package residue.
    with Path(__file__).open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                "another complete reproduction is already running in this extraction",
                file=sys.stderr,
            )
            return 2
        return _run_locked()


if __name__ == "__main__":
    raise SystemExit(main())
