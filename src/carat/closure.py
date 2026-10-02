"""Origin-sealed accounting windows.

A sealed window makes a deliberately narrow finality statement.  For a fixed
origin roster, every origin declares the complete local coordinate interval
assigned to one batch.  A result is final only after all declarations and every
coordinate they cover are present and the ordinary accounting constraints
accept the batch.  Seals are trusted origin declarations; this module provides
no authentication or Byzantine-origin protection.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .decode import decode
from .facts import Fact, canonical_pair, split_event
from .independent_check import check

MAX_MISSING_COORDINATE_DIAGNOSTICS = 64


@dataclass(frozen=True)
class SealedResult:
    batch: str
    final: bool
    closure_complete: bool
    accounting_determined: bool
    expected_origins: tuple[str, ...]
    sealed_origins: tuple[str, ...]
    data_facts: int
    seals: int
    unique_units: int
    gross_mass: int
    violations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "final": self.final,
            "closure_complete": self.closure_complete,
            "accounting_determined": self.accounting_determined,
            "expected_origins": list(self.expected_origins),
            "sealed_origins": list(self.sealed_origins),
            "data_facts": self.data_facts,
            "seals": self.seals,
            "unique_units": self.unique_units,
            "gross_mass": self.gross_mass,
            "violations": list(self.violations),
        }


def _roster(origins: Iterable[str]) -> tuple[str, ...]:
    values = tuple(origins)
    if not values or any(type(value) is not str or not value or ":" in value for value in values):
        raise ValueError("expected origins must be non-empty event-origin strings")
    if len(values) != len(set(values)):
        raise ValueError("expected origins must be unique")
    return tuple(sorted(values))


def _bounded_missing_coordinate_diagnostics(
    origin: str,
    previous: int,
    frontier: int,
    observed: Iterable[int],
) -> list[str]:
    """Describe a missing interval without expanding the entire frontier.

    The shape validator already bounds seal span.  This helper additionally
    bounds diagnostic material and derives gaps from observed coordinates, so a
    malformed large frontier cannot trigger one diagnostic per integer.
    """

    present = sorted({value for value in observed if previous < value <= frontier})
    missing_total = frontier - previous - len(present)
    if missing_total <= 0:
        return []
    output: list[str] = []
    cursor = previous + 1
    for value in present + [frontier + 1]:
        if value > cursor and len(output) < MAX_MISSING_COORDINATE_DIAGNOSTICS:
            take = min(value - cursor, MAX_MISSING_COORDINATE_DIAGNOSTICS - len(output))
            output.extend(
                f"missing-sealed-coordinate:{origin}:{sequence}"
                for sequence in range(cursor, cursor + take)
            )
        cursor = max(cursor, value + 1)
    if missing_total > len(output):
        output.append(
            f"missing-sealed-coordinate-count:{origin}:{missing_total}"
        )
    return output


def sealed_query(
    values: Iterable[str | Fact], batch: str, origins: Iterable[str]
) -> SealedResult:
    """Return a final batch result only after its declared coordinate closure.

    The current bounded contract requires the batch to be self-contained: every
    mint, presentation, transformation and effect dependency used by the batch
    must also be in the batch.  Facts from later batches may coexist in the
    replica and do not change a final result for this batch.
    """

    if type(batch) is not str or not batch:
        raise ValueError("batch must be a non-empty string")
    roster = _roster(origins)
    normalized: dict[str, Fact] = {}
    for value in values:
        text, fact = canonical_pair(value)
        normalized[text] = fact
    parsed = [normalized[text] for text in sorted(normalized)]
    violations: list[str] = []

    events: dict[str, list[Fact]] = defaultdict(list)
    seal_ids: dict[str, list[Fact]] = defaultdict(list)
    global_ids: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    seals_by_origin: dict[str, list[Fact]] = defaultdict(list)
    data_by_origin_sequence: dict[tuple[str, int], list[Fact]] = defaultdict(list)
    batch_data: list[Fact] = []

    for fact in parsed:
        events[fact["event"]].append(fact)
        origin, sequence = split_event(fact["event"])
        if fact["kind"] == "seal":
            seal_ids[fact["seal"]].append(fact)
            if fact["batch"] == batch:
                seals_by_origin[fact["origin"]].append(fact)
        else:
            identifier_field = {
                "mint": "unit",
                "presentation": "presentation",
                "transform": "transform",
                "effect": "effect",
            }[fact["kind"]]
            global_ids[(fact["kind"], fact[identifier_field])].append(fact)
            data_by_origin_sequence[(origin, sequence)].append(fact)
            if fact.get("batch") == batch:
                batch_data.append(fact)

    for event, entries in events.items():
        if len(entries) != 1:
            violations.append(f"event-equivocation:{event}")
    for seal_id, entries in seal_ids.items():
        if len(entries) != 1:
            violations.append(f"seal-equivocation:{seal_id}")

    # Accounting identifiers are globally scoped even though the ordinary
    # constraint check below is intentionally self-contained to one batch.
    # A later batch may therefore not silently reuse an identifier finalized
    # by this batch.  Such an extension is inadmissible and revokes finality.
    batch_keys = set()
    for fact in batch_data:
        identifier_field = {
            "mint": "unit",
            "presentation": "presentation",
            "transform": "transform",
            "effect": "effect",
        }[fact["kind"]]
        batch_keys.add((fact["kind"], fact[identifier_field]))
    for kind, identifier in sorted(batch_keys):
        definitions = global_ids[(kind, identifier)]
        if len(definitions) > 1:
            violations.append(f"global-{kind}-reuse:{identifier}")

    roster_set = set(roster)
    for origin in sorted(set(seals_by_origin) - roster_set):
        violations.append(f"unexpected-seal-origin:{origin}")
    for fact in batch_data:
        origin, _sequence = split_event(fact["event"])
        if origin not in roster_set:
            violations.append(f"unexpected-data-origin:{origin}")

    sealed_origins: list[str] = []
    intervals: dict[str, tuple[int, int]] = {}
    for origin in roster:
        entries = seals_by_origin.get(origin, [])
        if not entries:
            violations.append(f"missing-seal:{origin}")
            continue
        if len(entries) != 1:
            violations.append(f"multiple-seals:{origin}")
            continue
        seal_fact = entries[0]
        sealed_origins.append(origin)
        previous = seal_fact["previous"]
        frontier = seal_fact["frontier"]
        intervals[origin] = (previous, frontier)

        # A non-genesis window must point at the immediately preceding seal
        # event.  Recursively following these strictly decreasing coordinates
        # prevents an origin from skipping an undeclared interval while still
        # allowing a sequence of independently finalized windows.
        cursor = seal_fact
        while cursor["previous"] > 0:
            predecessor_event = f"{origin}:{cursor['previous']}"
            predecessors = events.get(predecessor_event, [])
            if not predecessors:
                violations.append(f"missing-predecessor-seal:{origin}:{cursor['previous']}")
                break
            if (len(predecessors) != 1 or predecessors[0]["kind"] != "seal"
                    or predecessors[0]["origin"] != origin):
                violations.append(f"invalid-predecessor-seal:{origin}:{cursor['previous']}")
                break
            cursor = predecessors[0]

        observed_sequences = [
            sequence
            for observed_origin, sequence in data_by_origin_sequence
            if observed_origin == origin and previous < sequence <= frontier
        ]
        violations.extend(
            _bounded_missing_coordinate_diagnostics(
                origin, previous, frontier, observed_sequences
            )
        )
        for sequence in observed_sequences:
            entries_at_coordinate = data_by_origin_sequence[(origin, sequence)]
            if len(entries_at_coordinate) != 1:
                violations.append(f"sealed-coordinate-equivocation:{origin}:{sequence}")
                continue
            covered = entries_at_coordinate[0]
            if covered.get("batch") != batch:
                violations.append(f"foreign-batch-coordinate:{origin}:{sequence}")

    for fact in batch_data:
        origin, sequence = split_event(fact["event"])
        interval = intervals.get(origin)
        if interval is None:
            continue
        previous, frontier = interval
        if not previous < sequence <= frontier:
            violations.append(f"fact-outside-sealed-interval:{origin}:{sequence}")

    decoded = decode(batch_data)
    checked = check(batch_data)
    if decoded.determined != checked.accepted:
        violations.append("decoder-checker-disagreement")
    if decoded.gross_mass != checked.gross_mass or decoded.unique_units != checked.unique_units:
        violations.append("decoder-checker-aggregate-disagreement")
    if not decoded.determined:
        violations.append("batch-accounting-undetermined")
    if not checked.accepted:
        violations.append("batch-checker-refusal")

    unique_violations = tuple(sorted(set(violations)))
    closure_complete = not any(
        value.startswith(
            (
                "missing-seal:",
                "multiple-seals:",
                "unexpected-seal-origin:",
                "unexpected-data-origin:",
                "missing-sealed-coordinate:",
                "missing-sealed-coordinate-count:",
                "sealed-coordinate-equivocation:",
                "foreign-batch-coordinate:",
                "fact-outside-sealed-interval:",
                "seal-equivocation:",
                "event-equivocation:",
                "missing-predecessor-seal:",
                "invalid-predecessor-seal:",
                "global-mint-reuse:",
                "global-presentation-reuse:",
                "global-transform-reuse:",
                "global-effect-reuse:",
            )
        )
        for value in unique_violations
    )
    final = not unique_violations
    return SealedResult(
        batch=batch,
        final=final,
        closure_complete=closure_complete,
        accounting_determined=decoded.determined and checked.accepted,
        expected_origins=roster,
        sealed_origins=tuple(sorted(sealed_origins)),
        data_facts=len(batch_data),
        seals=sum(len(entries) for entries in seals_by_origin.values()),
        unique_units=decoded.unique_units,
        gross_mass=decoded.gross_mass,
        violations=unique_violations,
    )
