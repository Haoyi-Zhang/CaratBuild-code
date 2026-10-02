"""Executed four-task DAG over the licensed public diff adapter."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from .decode import decode
from .facts import effect
from .independent_check import check
from .public_pair import extract_pair


TASKS = (
    ("prepare", ()),
    ("verify", ("prepare",)),
    ("package", ("verify",)),
    ("test", ("package",)),
)


def _run(command: list[str], root: Path) -> tuple[int, float]:
    start = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    return completed.returncode, time.perf_counter() - start


def _scenario_input(source: dict[str, Any], scenario: str) -> dict[str, Any]:
    value = deepcopy(source)
    if scenario == "changed-payload":
        value["target"][1]["hunk"] = value["target"][1]["hunk"].replace(
            'name.endswith("conftest.py")', 'name.endswith("other.py")'
        )
    return value


def run_executable_adapter(root: Path) -> dict[str, Any]:
    source = json.loads((root / "external_inputs" / "pytest_pair.json").read_text(encoding="utf-8"))
    worker = root / "scripts" / "build_task.py"
    rows: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    effect_sequence = 1

    for scenario in ("declared-backport", "changed-payload", "corrupted-package"):
        with tempfile.TemporaryDirectory(prefix="carat-build-") as temporary:
            directory = Path(temporary)
            input_path = directory / "input.json"
            facts_path = directory / "facts.json"
            report_path = directory / "report.json"
            package_path = directory / "package.json"
            result_path = directory / "result.json"
            input_path.write_text(
                json.dumps(_scenario_input(source, scenario), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            commands = {
                "prepare": [sys.executable, str(worker), "prepare", "--input", str(input_path), "--facts", str(facts_path)],
                "verify": [sys.executable, str(worker), "verify", "--facts", str(facts_path), "--report", str(report_path)],
                "package": [
                    sys.executable, str(worker), "package", "--facts", str(facts_path),
                    "--report", str(report_path), "--output", str(package_path),
                ] + (["--wrong-mass"] if scenario == "corrupted-package" else []),
                "test": [sys.executable, str(worker), "test", "--package", str(package_path), "--result", str(result_path)],
            }
            state: dict[str, str] = {}
            for task, dependencies in TASKS:
                if any(state[dependency] != "pass" for dependency in dependencies):
                    status, return_code, elapsed = "skip", None, 0.0
                else:
                    return_code, elapsed = _run(commands[task], root)
                    status = "pass" if return_code == 0 else "fail"
                state[task] = status
                rows.append({
                    "scenario": scenario,
                    "task": task,
                    "dependencies": ";".join(dependencies),
                    "outcome": status,
                    "return_code": "" if return_code is None else return_code,
                    "elapsed_ms": round(elapsed * 1000, 3),
                })
                effects.append(effect(
                    f"n2:{effect_sequence}",
                    f"build-{scenario}-{task}",
                    "public-target",
                    f"public-unit-{1 + ((effect_sequence - 1) % 3)}",
                    status,
                    ";".join(dependencies) if dependencies else "root",
                    "public-pair",
                ))
                effect_sequence += 1

            expected = {
                "declared-backport": {"prepare": "pass", "verify": "pass", "package": "pass", "test": "pass"},
                "changed-payload": {"prepare": "fail", "verify": "skip", "package": "skip", "test": "skip"},
                "corrupted-package": {"prepare": "pass", "verify": "pass", "package": "pass", "test": "fail"},
            }[scenario]
            if state != expected:
                raise AssertionError(f"unexpected executable build state for {scenario}: {state}")

    # The adapter checker links every recorded process outcome to one effect and
    # verifies dependency-respecting skip behavior.  Core accounting still treats
    # the dependency field as opaque outside this adapter.
    by_scenario = {(row["scenario"], row["task"]): row for row in rows}
    for scenario in ("declared-backport", "changed-payload", "corrupted-package"):
        for task, dependencies in TASKS:
            row = by_scenario[(scenario, task)]
            if row["outcome"] == "skip" and not any(
                by_scenario[(scenario, dependency)]["outcome"] != "pass"
                for dependency in dependencies
            ):
                raise AssertionError("build trace contains an unjustified skip")
            if row["outcome"] != "skip" and any(
                by_scenario[(scenario, dependency)]["outcome"] != "pass"
                for dependency in dependencies
            ):
                raise AssertionError("build task ran after a failed dependency")

    public_facts = extract_pair(source)
    combined = public_facts + effects
    decoded = decode(combined)
    checked = check(combined)
    if not decoded.determined or not checked.accepted or decoded.gross_mass != 15:
        raise AssertionError("executed effects changed or invalidated public-pair accounting")

    return {
        "scenarios": 3,
        "task_processes": len(rows) - sum(row["outcome"] == "skip" for row in rows),
        "task_records": len(rows),
        "pass_records": sum(row["outcome"] == "pass" for row in rows),
        "fail_records": sum(row["outcome"] == "fail" for row in rows),
        "skip_records": sum(row["outcome"] == "skip" for row in rows),
        "effect_facts": len(effects),
        "accounting_mass": decoded.gross_mass,
        "checker_accepted": checked.accepted,
        "rows": rows,
        "scope": "actual local subprocess DAG over the licensed diff adapter; not an upstream pytest build or a general dependency-causality proof",
    }
