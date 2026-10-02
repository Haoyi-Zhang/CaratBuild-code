#!/usr/bin/env python3
"""Offline, fail-closed distributable-tree audit."""
from __future__ import annotations

from pathlib import Path
import csv
import json
import os
import re
import subprocess
import sys


def audit(root: Path, *, require_generated: bool = True) -> dict[str, object]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    parsed = {"json": 0, "csv": 0}
    results = root / "results"
    for path in sorted(results.rglob("*")) if results.exists() else []:
        if not path.is_file():
            continue
        try:
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
                parsed["json"] += 1
            elif path.suffix == ".csv":
                with path.open(newline="", encoding="utf-8") as handle:
                    list(csv.reader(handle))
                parsed["csv"] += 1
        except Exception as exc:  # audit must report malformed retained evidence
            errors.append(f"unparseable result {path.relative_to(root)}: {exc}")

    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"symlink not permitted: {relative}")
        if path.is_dir() and path.name in {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}:
            errors.append(f"cache/VCS directory: {relative}")
        if path.is_file() and (path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store"):
            errors.append(f"generated/cache file: {relative}")

    reproduce = root / "scripts" / "reproduce.sh"
    if not reproduce.is_file():
        errors.append("missing scripts/reproduce.sh")
    elif not os.access(reproduce, os.X_OK):
        errors.append("scripts/reproduce.sh is not executable")
    else:
        text = reproduce.read_text(encoding="utf-8")
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if re.match(r"^\s*exec\s+", line):
                later = [item for item in lines[index + 1:] if item.strip() and not item.lstrip().startswith("#")]
                if later:
                    errors.append("premature exec makes later reproduction checks unreachable")
        shell = subprocess.run(
            ["bash", "-n", str(reproduce)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if shell.returncode:
            errors.append("reproduce.sh shell syntax error: " + shell.stdout[-1000:])

    scientific: list[str] = []
    if results.exists():
        for path in results.rglob("*"):
            if not path.is_file() or path.suffix not in {".json", ".csv"}:
                continue
            low = path.name.lower()
            if any(word in low for word in ("audit", "environment", "trial", "resource", "performance", "timing")):
                continue
            scientific.append(str(path.relative_to(root)))
    if require_generated and len(scientific) < 5:
        errors.append(f"too few non-audit scientific result files: {len(scientific)}")

    verifier = root / "scripts" / "verify_public_inputs.py"
    if verifier.exists():
        completed = subprocess.run(
            [sys.executable, str(verifier)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode:
            errors.append("public input verification failed: " + completed.stdout[-1000:])
    else:
        warnings.append("no public-input verifier")

    private_patterns = [r"/home/[^/\s]+/", r"/Users/[^/\s]+/", r"[A-Za-z]:\\Users\\[^\\\s]+\\"]
    excluded = {Path(__file__).resolve(), (root / "scripts" / "sanitize_outputs.py").resolve()}
    for path in root.rglob("*"):
        if path.resolve() in excluded or not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        if path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if any(re.search(pattern, text) for pattern in private_patterns):
            errors.append(f"private absolute path in {path.relative_to(root)}")

    return {
        "schema_version": 1,
        "status": "PASS" if not errors else "FAIL",
        "ok": not errors,
        "failures": errors,
        "errors": errors,
        "warnings": warnings,
        "parsed_results": parsed,
        "file_count": sum(1 for path in root.rglob("*") if path.is_file()),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = audit(root, require_generated=True)
    (root / "results").mkdir(exist_ok=True)
    (root / "results" / "reviewer_audit.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
