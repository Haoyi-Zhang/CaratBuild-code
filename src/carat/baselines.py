"""Occurrence-based and arrival-order branch baselines used in the evaluation."""

from __future__ import annotations

from typing import Iterable

from .facts import Fact, canonical_pair, canonical_text


def _facts(values: Iterable[str | Fact]) -> list[Fact]:
    return [canonical_pair(value)[1] for value in values]


def commit_count(values: Iterable[str | Fact]) -> int:
    return sum(1 for fact in _facts(values) if fact["kind"] == "presentation")


def presentation_mass(values: Iterable[str | Fact]) -> int:
    facts = _facts(values)
    masses = {
        fact["unit"]: int(fact["mass"])
        for fact in facts
        if fact["kind"] == "mint"
    }
    total = 0
    for fact in facts:
        if fact["kind"] != "presentation":
            continue
        for unit, _sign in fact["atoms"]:
            total += masses.get(unit, 0)
    return total


def arrival_order_branch_mass(arrival_order: Iterable[str | Fact]) -> int:
    facts = [canonical_pair(value)[1] for value in arrival_order]
    masses = {
        fact["unit"]: int(fact["mass"])
        for fact in facts
        if fact["kind"] == "mint"
    }
    latest_by_branch: dict[str, Fact] = {}
    for fact in facts:
        if fact["kind"] == "presentation":
            latest_by_branch[fact["branch"]] = fact
    total = 0
    for fact in latest_by_branch.values():
        for unit, _sign in fact["atoms"]:
            total += masses.get(unit, 0)
    return total


def centralized_exact(values: Iterable[str | Fact], expected_events: int | None = None) -> int | None:
    facts = _facts(values)
    if expected_events is not None and len({canonical_text(fact) for fact in facts}) != expected_events:
        return None
    mints: dict[str, int] = {}
    for fact in facts:
        if fact["kind"] != "mint":
            continue
        if fact["unit"] in mints and mints[fact["unit"]] != int(fact["mass"]):
            return None
        mints[fact["unit"]] = int(fact["mass"])
    return sum(mints.values())


def unique_mint_mass(values: Iterable[str | Fact]) -> int | None:
    """Identity-only accounting, without history or effect validation.

    This is the strong conservation baseline. It deduplicates identical facts
    and refuses multiple mint definitions of one unit, but deliberately never
    examines a presentation, transformation, or effect. It returns only a mass,
    not a certificate that the surrounding history is well formed.
    """
    definitions: dict[str, set[str]] = {}
    masses: dict[str, int] = {}
    for value in values:
        text, fact = canonical_pair(value)
        if fact["kind"] != "mint":
            continue
        definitions.setdefault(fact["unit"], set()).add(text)
        masses[fact["unit"]] = fact["mass"]
    if any(len(items) != 1 for items in definitions.values()):
        return None
    return sum(masses.values())
