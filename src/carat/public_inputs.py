"""Offline provenance checks for the retained public patch inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_PAIR_FILES = ("pr-14018.diff", "pr-14074.diff", "LICENSE.pytest")


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read provenance manifest: {path}") from exc
    if type(value) is not dict:
        raise ValueError(f"provenance manifest must be an object: {path}")
    return value


def verify_pytest_pair(directory: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = directory / "PROVENANCE.json"
    if not manifest_path.is_file():
        return [f"missing provenance manifest: {manifest_path}"]
    try:
        manifest = _json_object(manifest_path)
    except ValueError as exc:
        return [str(exc)]
    rows = manifest.get("files")
    if type(rows) is not list:
        return ["pytest provenance files must be a list"]
    by_name = {
        row.get("file"): row for row in rows
        if type(row) is dict and type(row.get("file")) is str
    }
    if set(by_name) != set(EXPECTED_PAIR_FILES):
        errors.append("pytest provenance file set mismatch")
    for name in EXPECTED_PAIR_FILES:
        path = directory / name
        row = by_name.get(name)
        if row is None:
            continue
        if not path.is_file():
            errors.append(f"missing public input: {path}")
            continue
        data = path.read_bytes()
        if row.get("bytes") != len(data):
            errors.append(f"byte-count mismatch: {path}")
        if row.get("sha256") != hashlib.sha256(data).hexdigest():
            errors.append(f"sha256 mismatch: {path}")
    for name in ("pr-14018.diff", "pr-14074.diff"):
        path = directory / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="strict")
            if not text.startswith("diff --git ") or "testing/test_config.py" not in text:
                errors.append(f"not the retained unified diff: {path}")
    license_path = directory / "LICENSE.pytest"
    if license_path.is_file() and "Permission is hereby granted" not in license_path.read_text(encoding="utf-8"):
        errors.append("pytest license text is incomplete")

    selection = manifest.get("selection")
    if type(selection) is not dict or selection.get("source_pr") != 14018 or selection.get("target_pr") != 14074:
        errors.append("pytest pair selection metadata mismatch")
    else:
        pair_rel = selection.get("pair_file")
        pair_path = (directory / pair_rel).resolve() if type(pair_rel) is str else None
        try:
            pair = _json_object(pair_path) if pair_path is not None else {}
        except ValueError as exc:
            errors.append(str(exc))
            pair = {}
        selected_paths = selection.get("selected_paths")
        if type(selected_paths) is not list or len(selected_paths) != 3:
            errors.append("pytest selected path set must contain exactly three files")
        for side, diff_name in (("source", "pr-14018.diff"), ("target", "pr-14074.diff")):
            rows = pair.get(side) if type(pair) is dict else None
            if type(rows) is not list or len(rows) != 3:
                errors.append(f"pytest pair {side} must contain exactly three hunks")
                continue
            diff_path = directory / diff_name
            diff_text = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""
            paths = [row.get("path") for row in rows if type(row) is dict]
            if selected_paths is not None and sorted(paths) != sorted(selected_paths):
                errors.append(f"pytest pair {side} paths disagree with provenance")
            for row in rows:
                if type(row) is not dict or type(row.get("hunk")) is not str or row["hunk"] not in diff_text:
                    errors.append(f"pytest pair {side} hunk is not verbatim in {diff_name}")
    return errors


def verify_public_patch_snapshots(directory: Path) -> list[str]:
    errors: list[str] = []
    metadata_path = directory / "SOURCE-METADATA.json"
    if not metadata_path.is_file():
        return [f"missing patch metadata: {metadata_path}"]
    try:
        metadata = _json_object(metadata_path)
    except ValueError as exc:
        return [str(exc)]
    license_name = metadata.get("license_file")
    license_path = directory / license_name if type(license_name) is str else None
    if license_path is None or not license_path.is_file():
        errors.append("public-patches license path is missing")
    else:
        license_data = license_path.read_bytes()
        if metadata.get("license_bytes") != len(license_data):
            errors.append("public-patches license byte-count mismatch")
        if metadata.get("license_sha256") != hashlib.sha256(license_data).hexdigest():
            errors.append("public-patches license digest mismatch")
        if b"Permission is hereby granted" not in license_data:
            errors.append("public-patches license text is incomplete")
    rows = metadata.get("patches")
    if type(rows) is not list:
        return errors + ["public-patches entries must be a list"]
    for row in rows:
        if type(row) is not dict or type(row.get("file")) is not str:
            errors.append("malformed public-patches entry")
            continue
        path = directory / row["file"]
        if not path.is_file():
            errors.append(f"missing patch snapshot: {path}")
            continue
        data = path.read_bytes()
        if row.get("bytes") != len(data):
            errors.append(f"byte-count mismatch: {path}")
        if row.get("sha256") != hashlib.sha256(data).hexdigest():
            errors.append(f"sha256 mismatch: {path}")
    return errors
