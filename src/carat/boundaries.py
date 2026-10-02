"""Small exact fixtures for transport, admission and compaction boundaries."""
from __future__ import annotations

from typing import Any
from .facts import canonical_text, mint
from .generate import mixed_history, ambiguity_variant
from .protocol import Replica


def transport_pair(name: str) -> tuple[Replica, Replica]:
    left, right = Replica("n0"), Replica("n1")
    if name in {"prefix-complete", "permanent-holes"}:
        late = {130, 132, 134, 136, 138, 140}
        for sequence in range(1, 142):
            fact = mint(f"n0:{sequence}", f"u{sequence}", 1, "n0", "window")
            if sequence % 2:
                left.append(fact)
                right.append(fact)
            elif name == "prefix-complete" or sequence in late:
                left.append(fact)
    elif name == "split-coordinate":
        left.append(mint("n0:1", "left-unit", 2, "n0", "window"))
        right.append(mint("n0:1", "right-unit", 3, "n0", "window"))
    else:
        raise ValueError("unknown transport fixture")
    return left, right


def service_fixture(name: str) -> tuple[list[list[dict[str, Any]]], bool]:
    slots: list[list[dict[str, Any]]] = [[] for _ in range(5)]
    if name == "mixed":
        scenario = mixed_history(31, 3, 901, rewrite_rounds=2)
        for index, fact in enumerate(scenario.facts):
            slots[index % 5].append(fact)
        return slots, True
    if name == "unresolved-effect":
        scenario = mixed_history(31, 3, 902, rewrite_rounds=2)
        facts = ambiguity_variant(scenario, "unresolved-effect", 1)
        for index, fact in enumerate(facts):
            slots[index % 5].append(fact)
        return slots, False
    if name in {"permanent-holes", "split-coordinate"}:
        left, right = transport_pair(name)
        slots[0] = left.facts_as_dicts()
        slots[1] = right.facts_as_dicts()
        return slots, name != "split-coordinate"
    raise ValueError("unknown service fixture")


def fixture_union(slots: list[list[dict[str, Any]]]) -> set[str]:
    return {canonical_text(fact) for slot in slots for fact in slot}
