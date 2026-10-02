"""Independent CARAT fact and witness checker.

The checker shares the public fact normalizer but deliberately does not import
or call the accounting decoder. It reconstructs the constraints and witness
predicates separately.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .facts import Fact, FactError, canonical_pair, parse_text, split_event

MAX_MISSING_COORDINATE_DIAGNOSTICS = 64


@dataclass(frozen=True)
class CheckerReport:
    accepted: bool
    violations: tuple[str, ...]
    unique_units: int
    gross_mass: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "violations": list(self.violations),
            "unique_units": self.unique_units,
            "gross_mass": self.gross_mass,
        }


@dataclass(frozen=True)
class SealedCheckerReport:
    batch: str
    accepted: bool
    closure_complete: bool
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
            "accepted": self.accepted,
            "closure_complete": self.closure_complete,
            "expected_origins": list(self.expected_origins),
            "sealed_origins": list(self.sealed_origins),
            "data_facts": self.data_facts,
            "seals": self.seals,
            "unique_units": self.unique_units,
            "gross_mass": self.gross_mass,
            "violations": list(self.violations),
        }


def _bounded_missing_coordinate_diagnostics(
    origin: str,
    previous: int,
    frontier: int,
    observed: Iterable[int],
) -> list[str]:
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
        output.append(f"missing-sealed-coordinate-count:{origin}:{missing_total}")
    return output


def _texts(values: Iterable[str | Fact]) -> list[str]:
    return sorted({canonical_pair(value)[0] for value in values})


def _atom_map(fact: Fact) -> Counter[tuple[str, int]]:
    return Counter((unit, sign) for unit, sign in fact["atoms"])


def _expected(transform: Fact, inputs: list[Fact]) -> list[Counter[tuple[str, int]]]:
    operation = transform["operation"]
    if operation in {"rebase", "cherry_pick"}:
        if len(inputs) != 1 or len(transform["outputs"]) != 1:
            return []
        return [_atom_map(inputs[0])]
    if operation == "fork":
        if len(inputs) != 1:
            return []
        value = _atom_map(inputs[0])
        return [value.copy() for _ in transform["outputs"]]
    if operation == "squash":
        if len(inputs) < 2 or len(transform["outputs"]) != 1:
            return []
        signs: dict[str, int] = {}
        for item in inputs:
            for unit, sign in item["atoms"]:
                previous = signs.get(unit)
                if previous is not None and previous != sign:
                    return []
                signs[unit] = sign
        return [Counter((unit, sign) for unit, sign in signs.items())]
    if operation == "revert":
        if len(inputs) != 1 or len(transform["outputs"]) != 1:
            return []
        return [Counter((unit, -sign) for unit, sign in _atom_map(inputs[0]))]
    return []


def check(values: Iterable[str | Fact]) -> CheckerReport:
    texts = _texts(values)
    parsed = [parse_text(text) for text in texts]
    violations: list[str] = []

    events: dict[str, list[Fact]] = defaultdict(list)
    mints: dict[str, list[Fact]] = defaultdict(list)
    presentations: dict[str, list[Fact]] = defaultdict(list)
    transforms: dict[str, list[Fact]] = defaultdict(list)
    effects: dict[str, list[Fact]] = defaultdict(list)

    for fact in parsed:
        events[fact["event"]].append(fact)
        kind = fact["kind"]
        if kind == "mint":
            mints[fact["unit"]].append(fact)
        elif kind == "presentation":
            presentations[fact["presentation"]].append(fact)
        elif kind == "transform":
            transforms[fact["transform"]].append(fact)
        elif kind == "effect":
            effects[fact["effect"]].append(fact)

    for event, facts in events.items():
        if len(facts) != 1:
            violations.append(f"event-equivocation:{event}")
    for unit, facts in mints.items():
        if len(facts) != 1:
            violations.append(f"unit-minted-more-than-once:{unit}")
    for presentation_id, facts in presentations.items():
        if len(facts) != 1:
            violations.append(f"presentation-equivocation:{presentation_id}")
    for transform_id, facts in transforms.items():
        if len(facts) != 1:
            violations.append(f"transform-equivocation:{transform_id}")
    for effect_id, facts in effects.items():
        if len(facts) != 1:
            violations.append(f"effect-equivocation:{effect_id}")

    unique_presentations = {
        key: entries[0] for key, entries in presentations.items() if len(entries) == 1
    }
    unique_mints = {key: entries[0] for key, entries in mints.items() if len(entries) == 1}
    unique_transforms = [entries[0] for entries in transforms.values() if len(entries) == 1]

    for presentation_id, fact in unique_presentations.items():
        for unit, _sign in fact["atoms"]:
            if unit not in mints:
                violations.append(f"presentation-missing-mint:{presentation_id}:{unit}")

    for transform_fact in unique_transforms:
        missing = [
            value
            for value in transform_fact["inputs"] + transform_fact["outputs"]
            if value not in unique_presentations
        ]
        if missing:
            violations.append(
                f"transform-missing-dependency:{transform_fact['transform']}:{missing[0]}"
            )
            continue
        inputs = [unique_presentations[value] for value in transform_fact["inputs"]]
        outputs = [unique_presentations[value] for value in transform_fact["outputs"]]
        expected = _expected(transform_fact, inputs)
        actual = [_atom_map(item) for item in outputs]
        if (
            not expected
            or len(expected) != len(actual)
            or any(left != right for left, right in zip(expected, actual))
        ):
            violations.append(f"transform-law-violation:{transform_fact['transform']}")

    for effect_id, entries in effects.items():
        if len(entries) != 1:
            continue
        fact = entries[0]
        presentation_fact = unique_presentations.get(fact["presentation"])
        if presentation_fact is None:
            violations.append(f"effect-missing-presentation:{effect_id}")
            continue
        if fact["unit"] not in {unit for unit, _ in presentation_fact["atoms"]}:
            violations.append(f"effect-unit-not-in-presentation:{effect_id}")
        if fact["unit"] not in mints:
            violations.append(f"effect-missing-mint:{effect_id}")

    gross_mass = sum(fact["mass"] for fact in unique_mints.values())
    return CheckerReport(
        accepted=not violations,
        violations=tuple(sorted(set(violations))),
        unique_units=len(unique_mints),
        gross_mass=gross_mass,
    )


def check_sealed(
    values: Iterable[str | Fact], batch: str, origins: Iterable[str]
) -> SealedCheckerReport:
    """Independently validate origin-sealed batch closure.

    This implementation deliberately does not import ``carat.closure``.  It
    rebuilds the roster, interval and aggregate checks from normalized facts.
    """

    if type(batch) is not str or not batch:
        raise ValueError("batch must be a non-empty string")
    roster_values = tuple(origins)
    if (
        not roster_values
        or any(type(value) is not str or not value or ":" in value for value in roster_values)
        or len(roster_values) != len(set(roster_values))
    ):
        raise ValueError("expected origins must be unique event-origin strings")
    roster = tuple(sorted(roster_values))
    roster_set = set(roster)

    normalized: dict[str, Fact] = {}
    for value in values:
        text, fact = canonical_pair(value)
        normalized[text] = fact
    parsed = [normalized[text] for text in sorted(normalized)]

    violations: list[str] = []
    event_entries: dict[str, list[Fact]] = defaultdict(list)
    seal_ids: dict[str, list[Fact]] = defaultdict(list)
    global_ids: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    origin_seals: dict[str, list[Fact]] = defaultdict(list)
    coordinate_entries: dict[tuple[str, int], list[Fact]] = defaultdict(list)
    data: list[Fact] = []

    for fact in parsed:
        event_entries[fact["event"]].append(fact)
        origin, sequence = split_event(fact["event"])
        if fact["kind"] == "seal":
            seal_ids[fact["seal"]].append(fact)
            if fact["batch"] == batch:
                origin_seals[fact["origin"]].append(fact)
        else:
            identifier_field = {
                "mint": "unit",
                "presentation": "presentation",
                "transform": "transform",
                "effect": "effect",
            }[fact["kind"]]
            global_ids[(fact["kind"], fact[identifier_field])].append(fact)
            coordinate_entries[(origin, sequence)].append(fact)
            if fact.get("batch") == batch:
                data.append(fact)

    for event, entries in event_entries.items():
        if len(entries) != 1:
            violations.append(f"event-equivocation:{event}")
    for seal_id, entries in seal_ids.items():
        if len(entries) != 1:
            violations.append(f"seal-equivocation:{seal_id}")
    batch_keys = set()
    for fact in data:
        identifier_field = {
            "mint": "unit",
            "presentation": "presentation",
            "transform": "transform",
            "effect": "effect",
        }[fact["kind"]]
        batch_keys.add((fact["kind"], fact[identifier_field]))
    for kind, identifier in sorted(batch_keys):
        if len(global_ids[(kind, identifier)]) > 1:
            violations.append(f"global-{kind}-reuse:{identifier}")
    for origin in sorted(set(origin_seals) - roster_set):
        violations.append(f"unexpected-seal-origin:{origin}")
    for fact in data:
        origin, _sequence = split_event(fact["event"])
        if origin not in roster_set:
            violations.append(f"unexpected-data-origin:{origin}")

    intervals: dict[str, tuple[int, int]] = {}
    sealed_origins: list[str] = []
    for origin in roster:
        seals = origin_seals.get(origin, [])
        if not seals:
            violations.append(f"missing-seal:{origin}")
            continue
        if len(seals) != 1:
            violations.append(f"multiple-seals:{origin}")
            continue
        declaration = seals[0]
        sealed_origins.append(origin)
        previous, frontier = declaration["previous"], declaration["frontier"]
        intervals[origin] = (previous, frontier)
        cursor = declaration
        while cursor["previous"] > 0:
            predecessor_event = f"{origin}:{cursor['previous']}"
            predecessors = event_entries.get(predecessor_event, [])
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
            for observed_origin, sequence in coordinate_entries
            if observed_origin == origin and previous < sequence <= frontier
        ]
        violations.extend(
            _bounded_missing_coordinate_diagnostics(
                origin, previous, frontier, observed_sequences
            )
        )
        for sequence in observed_sequences:
            covered = coordinate_entries[(origin, sequence)]
            if len(covered) != 1:
                violations.append(f"sealed-coordinate-equivocation:{origin}:{sequence}")
            elif covered[0].get("batch") != batch:
                violations.append(f"foreign-batch-coordinate:{origin}:{sequence}")

    for fact in data:
        origin, sequence = split_event(fact["event"])
        interval = intervals.get(origin)
        if interval is not None and not interval[0] < sequence <= interval[1]:
            violations.append(f"fact-outside-sealed-interval:{origin}:{sequence}")

    base = check(data)
    if not base.accepted:
        violations.append("batch-checker-refusal")
    unique_violations = tuple(sorted(set(violations)))
    closure_complete = not any(
        item.startswith((
            "event-equivocation:", "seal-equivocation:", "unexpected-seal-origin:",
            "unexpected-data-origin:", "missing-seal:", "multiple-seals:",
            "missing-sealed-coordinate:", "missing-sealed-coordinate-count:",
            "sealed-coordinate-equivocation:",
            "foreign-batch-coordinate:", "fact-outside-sealed-interval:",
            "missing-predecessor-seal:", "invalid-predecessor-seal:",
            "global-mint-reuse:", "global-presentation-reuse:",
            "global-transform-reuse:", "global-effect-reuse:"
        ))
        for item in unique_violations
    )
    return SealedCheckerReport(
        batch=batch,
        accepted=not unique_violations,
        closure_complete=closure_complete,
        expected_origins=roster,
        sealed_origins=tuple(sorted(sealed_origins)),
        data_facts=len(data),
        seals=sum(len(entries) for entries in origin_seals.values()),
        unique_units=base.unique_units,
        gross_mass=base.gross_mass,
        violations=unique_violations,
    )


def _label_absent(label: str, all_facts: list[Fact]) -> bool:
    kind, separator, value = label.partition(":")
    if not separator or not value:
        return False
    if kind == "mint":
        return sum(1 for fact in all_facts if fact["kind"] == "mint" and fact["unit"] == value) != 1
    if kind == "presentation":
        return sum(
            1
            for fact in all_facts
            if fact["kind"] == "presentation" and fact["presentation"] == value
        ) != 1
    return False


def _predicate(code: str, selected: list[Fact], missing: list[str]) -> bool:
    if code == "event-equivocation":
        return len(selected) == 2 and selected[0]["event"] == selected[1]["event"] and selected[0] != selected[1]
    if code == "unit-minted-more-than-once":
        return (
            len(selected) == 2
            and all(item["kind"] == "mint" for item in selected)
            and selected[0]["unit"] == selected[1]["unit"]
            and selected[0] != selected[1]
        )
    if code == "presentation-equivocation":
        return (
            len(selected) == 2
            and all(item["kind"] == "presentation" for item in selected)
            and selected[0]["presentation"] == selected[1]["presentation"]
            and selected[0] != selected[1]
        )
    if code == "transform-equivocation":
        return (
            len(selected) == 2
            and all(item["kind"] == "transform" for item in selected)
            and selected[0]["transform"] == selected[1]["transform"]
            and selected[0] != selected[1]
        )
    if code == "effect-equivocation":
        return (
            len(selected) == 2
            and all(item["kind"] == "effect" for item in selected)
            and selected[0]["effect"] == selected[1]["effect"]
            and selected[0] != selected[1]
        )
    if code == "presentation-missing-mint":
        return (
            len(selected) == 1
            and selected[0]["kind"] == "presentation"
            and len(missing) == 1
            and missing[0].startswith("mint:")
            and missing[0].split(":", 1)[1] in {unit for unit, _ in selected[0]["atoms"]}
        )
    if code == "transform-missing-dependency":
        if len(selected) != 1 or selected[0]["kind"] != "transform" or len(missing) != 1:
            return False
        transform_fact = selected[0]
        label = missing[0]
        if label.startswith("presentation:"):
            value = label.split(":", 1)[1]
            return value in transform_fact["inputs"] + transform_fact["outputs"]
        return False
    if code == "effect-missing-presentation":
        return (
            len(selected) == 1
            and selected[0]["kind"] == "effect"
            and missing == [f"presentation:{selected[0]['presentation']}"]
        )
    if code == "effect-missing-mint":
        return (
            len(selected) == 1
            and selected[0]["kind"] == "effect"
            and missing == [f"mint:{selected[0]['unit']}"]
        )
    if code == "effect-unit-not-in-presentation":
        if len(selected) != 2:
            return False
        effect_fact = next((item for item in selected if item["kind"] == "effect"), None)
        presentation_fact = next((item for item in selected if item["kind"] == "presentation"), None)
        return bool(
            effect_fact
            and presentation_fact
            and effect_fact["presentation"] == presentation_fact["presentation"]
            and effect_fact["unit"] not in {unit for unit, _ in presentation_fact["atoms"]}
        )
    if code == "transform-law-violation":
        transform_facts = [item for item in selected if item["kind"] == "transform"]
        presentation_facts = {
            item["presentation"]: item for item in selected if item["kind"] == "presentation"
        }
        if len(transform_facts) != 1:
            return False
        transform_fact = transform_facts[0]
        refs = transform_fact["inputs"] + transform_fact["outputs"]
        if any(value not in presentation_facts for value in refs):
            return False
        inputs = [presentation_facts[value] for value in transform_fact["inputs"]]
        outputs = [presentation_facts[value] for value in transform_fact["outputs"]]
        expected = _expected(transform_fact, inputs)
        actual = [_atom_map(item) for item in outputs]
        return (
            not expected
            or len(expected) != len(actual)
            or any(left != right for left, right in zip(expected, actual))
        )
    return False


_CODE_CATEGORY = {
    "event-equivocation": "conflicting-equivalence",
    "unit-minted-more-than-once": "conflicting-equivalence",
    "presentation-equivocation": "conflicting-equivalence",
    "transform-equivocation": "conflicting-equivalence",
    "effect-equivocation": "conflicting-equivalence",
    "transform-law-violation": "conflicting-equivalence",
    "presentation-missing-mint": "missing-event",
    "transform-missing-dependency": "missing-event",
    "effect-missing-presentation": "unresolved-effect",
    "effect-missing-mint": "unresolved-effect",
    "effect-unit-not-in-presentation": "unresolved-effect",
}


def verify_witness(
    all_values: Iterable[str | Fact], witness: dict[str, Any]
) -> tuple[bool, str]:
    required = {"category", "code", "facts", "missing", "detail"}
    if type(witness) is not dict or set(witness) != required:
        return False, "witness must have exactly the public fields"
    category = witness["category"]
    code = witness["code"]
    detail = witness["detail"]
    selected_value = witness["facts"]
    missing_value = witness["missing"]
    if type(category) is not str or not category or type(code) is not str or not code:
        return False, "witness category and code must be non-empty strings"
    if type(detail) is not str or not detail:
        return False, "witness detail must be a non-empty string"
    if type(selected_value) is not list or type(missing_value) is not list:
        return False, "witness facts and missing references must be lists"
    if any(type(item) is not str or not item for item in selected_value + missing_value):
        return False, "witness entries must be non-empty strings"
    if len(selected_value) != len(set(selected_value)) or len(missing_value) != len(set(missing_value)):
        return False, "witness cannot repeat a fact or missing reference"
    if _CODE_CATEGORY.get(code) != category:
        return False, "witness category does not match its code"

    try:
        all_texts = _texts(all_values)
        all_set = set(all_texts)
        all_facts = [parse_text(text) for text in all_texts]
    except FactError as exc:
        return False, f"observed fact set is malformed: {exc}"

    selected_texts = list(selected_value)
    missing = list(missing_value)
    if not selected_texts or any(text not in all_set for text in selected_texts):
        return False, "witness cites an unavailable or non-normalized fact"
    if any(not _label_absent(label, all_facts) for label in missing):
        return False, "a reported missing reference is present or malformed"
    selected = [parse_text(text) for text in selected_texts]
    if not _predicate(code, selected, missing):
        return False, "selected facts do not establish the reported violation"
    for index in range(len(selected)):
        reduced = selected[:index] + selected[index + 1 :]
        if _predicate(code, reduced, missing):
            return False, "a cited fact is unnecessary"
    for index in range(len(missing)):
        reduced_missing = missing[:index] + missing[index + 1 :]
        if _predicate(code, selected, reduced_missing):
            return False, "a missing reference is unnecessary"
    return True, "witness is valid and inclusion-minimal"
