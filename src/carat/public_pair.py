"""Narrow static extraction for a declared three-file backport pair.

Hunk coordinates are not identities. Matching ignores only those coordinates
and unchanged context; every changed line, including whitespace, remains exact.
The adapter is format-generic within this finite contract: it does not whitelist
pytest paths, URLs, or payload bytes.  The packaged pytest pair is one evaluated
input whose provenance is checked separately.  This is a text-preserving
declared-pair adapter, not semantic equivalence or a build execution engine.
Upstream snippets are read as data only.
"""
from __future__ import annotations
import re
from typing import Any
from .facts import mint, presentation, transform

_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[^\r\n]*)")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_TOP_LEVEL = {
    "relation", "repository", "access_date", "source_record",
    "target_record", "source", "target",
}


def _metadata(corpus: dict[str, Any]) -> None:
    if set(corpus) != _TOP_LEVEL or corpus.get("relation") != "explicit-backport":
        raise ValueError("an explicit finite external relation is required")
    repository = corpus["repository"]
    access_date = corpus["access_date"]
    source_record = corpus["source_record"]
    target_record = corpus["target_record"]
    if type(repository) is not str or not repository or len(repository.encode()) > 256:
        raise ValueError("repository metadata must be a bounded non-empty string")
    if type(access_date) is not str or _DATE.fullmatch(access_date) is None:
        raise ValueError("access_date must be an ISO calendar-date string")
    for label, value in (("source_record", source_record), ("target_record", target_record)):
        if type(value) is not str or not value.startswith(("https://", "http://")) or len(value.encode()) > 2048:
            raise ValueError(f"{label} must be a bounded HTTP(S) record locator")


def payload(hunk: str) -> tuple[str, ...]:
    if type(hunk) is not str or not hunk.endswith("\n") or len(hunk.encode()) > 32768:
        raise ValueError("hunk must be bounded newline-terminated text")
    lines = hunk.splitlines()
    if not lines or not (match := _HEADER.fullmatch(lines[0])):
        raise ValueError("only one ordinary unified-text hunk is supported")
    old_expected = int(match[2]) if match[2] is not None else 1
    new_expected = int(match[4]) if match[4] is not None else 1
    if old_expected > 512 or new_expected > 512:
        raise ValueError("hunk line-count limit exceeded")
    old = new = 0
    changes = []
    for line in lines[1:]:
        if not line or line[0] not in " +-":
            raise ValueError("unsupported or truncated diff record")
        if line[0] != "+": old += 1
        if line[0] != "-": new += 1
        if line[0] != " ": changes.append(line)
    if (old, new) != (old_expected, new_expected) or not changes:
        raise ValueError("hunk counts do not match complete nonempty payload")
    return tuple(changes)


def extract_pair(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    if type(corpus) is not dict:
        raise ValueError("declared pair must be an object")
    _metadata(corpus)
    # This deliberately narrow adapter admits three selected files, not an
    # arbitrary repository or unbounded history. It never invents fresh target
    # work when exact extraction fails.
    source, target = corpus.get("source"), corpus.get("target")
    if type(source) is not list or type(target) is not list or len(source) != 3 or len(target) != 3:
        raise ValueError("exactly three selected file hunks are required")
    def items(values: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
        result = {}
        for item in values:
            if type(item) is not dict or set(item) != {"path", "hunk"}:
                raise ValueError("unexpected selected-hunk fields")
            path = item["path"]
            if type(path) is not str or not path or path.startswith("/") or ".." in path.split("/") or path in result:
                raise ValueError("invalid or duplicate source path")
            result[path] = payload(item["hunk"])
        return result
    left, right = items(source), items(target)
    if left != right:
        raise ValueError("declared-pair changed-line payloads differ")
    facts, atoms = [], []
    for index, (_path, lines) in enumerate(sorted(left.items()), 1):
        unit = f"public-unit-{index}"
        facts.append(mint(f"n0:{index}", unit, len(lines), "n0", "public-pair"))
        atoms.append((unit, 1))
    facts += [presentation("n0:4", "public-source", atoms, "source", 0, "public-pair"),
              presentation("n1:1", "public-target", atoms, "target", 1, "public-pair"),
              transform("n1:2", "public-relation", "cherry_pick", ["public-source"],
                        ["public-target"], [], "public-pair")]
    return facts
