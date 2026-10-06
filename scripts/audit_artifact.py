#!/usr/bin/env python3
"""Fail closed when the delivered artifact surfaces disagree.

This audit is intentionally artifact-local: the standalone repository does not
contain the manuscript.  A separate packaging check cross-validates the paper's
bibliography and page contract before delivery.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results" / "summary"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def require_paths(cell: str) -> None:
    for raw in cell.split(";"):
        token = raw.strip()
        if not token:
            continue
        token = token.split(" §", 1)[0].strip()
        # Section/table labels are not filesystem paths.
        if "/" not in token and not token.endswith((".md", ".py", ".csv", ".json")):
            continue
        if not (ROOT / token).exists():
            raise AssertionError(f"ledger path is missing: {token}")


def main() -> int:
    complete = load_json(SUMMARY / "complete_overview.json")
    tests = load_json(SUMMARY / "unit_tests.json")
    if (
        not tests.get("successful")
        or tests.get("tests_run") != 72
        or tests.get("module_level_tests_run", 0) < 8
    ):
        raise AssertionError("the complete deterministic test surface did not pass")
    if not complete.get("documented_commands_completed") or not complete.get("scientific_completion"):
        raise AssertionError("the result collector did not close the bounded scientific scope")
    if complete.get("venue_submission_ready") is not False:
        raise AssertionError("the internal package must not claim external submission readiness")

    with (ROOT / "claim_evidence_ledger.csv").open(newline="", encoding="utf-8") as handle:
        claims = list(csv.DictReader(handle))
    if len(claims) < 25 or len({row["claim_id"] for row in claims}) != len(claims):
        raise AssertionError("claim-evidence ledger is incomplete or has duplicate identifiers")
    for row in claims:
        require_paths(row["raw_result_path"])
        require_paths(row["proof_or_checker"])
        require_paths(row["source_or_test"])
        if not row["fresh_self_recheck_status"].strip():
            raise AssertionError(f"claim lacks a fresh recheck state: {row['claim_id']}")

    # Bibliography verification belongs to the manuscript package.  This
    # standalone code artifact intentionally has no unconditional dependency on
    # a paper-side ``reference_audit.csv``.  Its external-resource ledger is
    # retained only for software/input provenance and must be nonempty.
    with (ROOT / "external_resources.csv").open(newline="", encoding="utf-8") as handle:
        resources = list(csv.DictReader(handle))
    if not resources or len({row["resource_id"] for row in resources}) != len(resources):
        raise AssertionError("external-resource ledger is empty or has duplicate identifiers")

    forbidden_suffixes = {".zip", ".tar", ".gz", ".pyc"}
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            raise AssertionError(f"symbolic link is not allowed: {path.relative_to(ROOT)}")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes:
            raise AssertionError(f"nested archive or cache is not allowed: {path.relative_to(ROOT)}")
        if path.name == "__pycache__":
            raise AssertionError("bytecode cache is not allowed")

    retention = complete["boundary_campaigns"]["multiprocess_retention"]
    if retention.get("centralized_union_used") is not False:
        raise AssertionError("five-process exchange regressed to a controller-computed union")
    if retention.get("pairwise_exchange_rounds", 0) < 3:
        raise AssertionError("partition/heal pairwise exchange evidence is missing")
    if not retention.get("endpoint_restart_before_heal"):
        raise AssertionError("pairwise exchange lacks the pre-heal restart")

    result = {
        "passed": True,
        "tests_run": tests["tests_run"],
        "claim_rows": len(claims),
        "manuscript_reference_audit": "not part of standalone code artifact",
        "external_resource_rows": len(resources),
        "pairwise_exchange_rounds": retention["pairwise_exchange_rounds"],
        "centralized_union_used": retention["centralized_union_used"],
        "scope": "artifact-local semantic, provenance, ledger, and packaging-surface audit",
    }
    (SUMMARY / "artifact_audit.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
