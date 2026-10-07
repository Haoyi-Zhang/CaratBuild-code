"""Deterministic accounting decoder and ambiguity-witness construction."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .facts import Fact, canonical_pair, parse_text


@dataclass(frozen=True)
class Witness:
    category: str
    code: str
    facts: tuple[str, ...]
    missing: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "code": self.code,
            "facts": list(self.facts),
            "missing": list(self.missing),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DecodeResult:
    determined: bool
    unique_units: int
    gross_mass: int
    presentations: int
    effects: int
    passing_effects: int
    failing_effects: int
    witnesses: tuple[Witness, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "determined": self.determined,
            "unique_units": self.unique_units,
            "gross_mass": self.gross_mass,
            "presentations": self.presentations,
            "effects": self.effects,
            "passing_effects": self.passing_effects,
            "failing_effects": self.failing_effects,
            "witnesses": [item.as_dict() for item in self.witnesses],
        }


def _texts(values: Iterable[str | Fact]) -> list[str]:
    return sorted({canonical_pair(value)[0] for value in values})


def _atom_counter(presentation: Fact) -> Counter[tuple[str, int]]:
    return Counter((unit, sign) for unit, sign in presentation["atoms"])


def _union_atoms(presentations: list[Fact]) -> Counter[tuple[str, int]] | None:
    signs: dict[str, int] = {}
    for item in presentations:
        for unit, sign in item["atoms"]:
            previous = signs.get(unit)
            if previous is not None and previous != sign:
                return None
            signs[unit] = sign
    return Counter((unit, sign) for unit, sign in signs.items())


def _expected_transform(transform: Fact, inputs: list[Fact]) -> list[Counter[tuple[str, int]]]:
    operation = transform["operation"]
    if operation in {"rebase", "cherry_pick"}:
        if len(inputs) != 1 or len(transform["outputs"]) != 1:
            return []
        return [_atom_counter(inputs[0])]
    if operation == "fork":
        if len(inputs) != 1:
            return []
        expected = _atom_counter(inputs[0])
        return [expected.copy() for _ in transform["outputs"]]
    if operation == "squash":
        if len(inputs) < 2 or len(transform["outputs"]) != 1:
            return []
        expected = _union_atoms(inputs)
        if expected is None:
            return []
        return [expected]
    if operation == "revert":
        if len(inputs) != 1 or len(transform["outputs"]) != 1:
            return []
        return [Counter((unit, -sign) for (unit, sign) in _atom_counter(inputs[0]))]
    return []


def decode(values: Iterable[str | Fact]) -> DecodeResult:
    texts = _texts(values)
    parsed = [(text, parse_text(text)) for text in texts]
    witnesses: list[Witness] = []

    by_event: dict[str, list[str]] = defaultdict(list)
    for text, fact in parsed:
        by_event[fact["event"]].append(text)
    for event, event_texts in sorted(by_event.items()):
        if len(event_texts) > 1:
            witnesses.append(
                Witness(
                    "conflicting-equivalence",
                    "event-equivocation",
                    tuple(sorted(event_texts)[:2]),
                    (),
                    f"event coordinate {event} carries two different facts",
                )
            )

    mints: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
    presentations: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
    transforms: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
    effects: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
    for text, fact in parsed:
        if fact["kind"] == "mint":
            mints[fact["unit"]].append((text, fact))
        elif fact["kind"] == "presentation":
            presentations[fact["presentation"]].append((text, fact))
        elif fact["kind"] == "transform":
            transforms[fact["transform"]].append((text, fact))
        elif fact["kind"] == "effect":
            effects[fact["effect"]].append((text, fact))

    valid_mints: dict[str, tuple[str, Fact]] = {}
    for unit, entries in sorted(mints.items()):
        if len(entries) > 1:
            witnesses.append(
                Witness(
                    "conflicting-equivalence",
                    "unit-minted-more-than-once",
                    tuple(sorted(text for text, _ in entries)[:2]),
                    (),
                    f"unit {unit} has more than one mint fact",
                )
            )
        else:
            valid_mints[unit] = entries[0]

    valid_presentations: dict[str, tuple[str, Fact]] = {}
    for presentation_id, entries in sorted(presentations.items()):
        if len(entries) > 1:
            witnesses.append(
                Witness(
                    "conflicting-equivalence",
                    "presentation-equivocation",
                    tuple(sorted(text for text, _ in entries)[:2]),
                    (),
                    f"presentation {presentation_id} has incompatible contents",
                )
            )
            continue
        text, fact = entries[0]
        valid_presentations[presentation_id] = (text, fact)
        for unit, _sign in fact["atoms"]:
            if unit not in mints:
                witnesses.append(
                    Witness(
                        "missing-event",
                        "presentation-missing-mint",
                        (text,),
                        (f"mint:{unit}",),
                        f"presentation {presentation_id} refers to unobserved unit {unit}",
                    )
                )

    valid_transforms: list[tuple[str, Fact]] = []
    for transform_id, entries in sorted(transforms.items()):
        if len(entries) > 1:
            witnesses.append(
                Witness(
                    "conflicting-equivalence",
                    "transform-equivocation",
                    tuple(sorted(text for text, _ in entries)[:2]),
                    (),
                    f"transform {transform_id} has incompatible declarations",
                )
            )
        else:
            valid_transforms.append(entries[0])

    for text, transform_fact in sorted(valid_transforms, key=lambda item: item[0]):
        missing: list[str] = []
        input_facts: list[Fact] = []
        output_facts: list[Fact] = []
        material: list[str] = [text]
        for presentation_id in transform_fact["inputs"]:
            entry = valid_presentations.get(presentation_id)
            if entry is None:
                missing.append(f"presentation:{presentation_id}")
            else:
                material.append(entry[0])
                input_facts.append(entry[1])
        for presentation_id in transform_fact["outputs"]:
            entry = valid_presentations.get(presentation_id)
            if entry is None:
                missing.append(f"presentation:{presentation_id}")
            else:
                material.append(entry[0])
                output_facts.append(entry[1])
        if missing:
            witnesses.append(
                Witness(
                    "missing-event",
                    "transform-missing-dependency",
                    (text,),
                    (sorted(set(missing))[0],),
                    f"transform {transform_fact['transform']} cannot be checked",
                )
            )
            continue

        expected = _expected_transform(transform_fact, input_facts)
        actual = [_atom_counter(item) for item in output_facts]
        law_holds = bool(expected) and len(expected) == len(actual) and all(
            left == right for left, right in zip(expected, actual)
        )
        if not law_holds:
            witnesses.append(
                Witness(
                    "conflicting-equivalence",
                    "transform-law-violation",
                    tuple(sorted(set(material))),
                    (),
                    f"transform {transform_fact['transform']} violates the declared {transform_fact['operation']} law",
                )
            )

    valid_effects: dict[str, tuple[str, Fact]] = {}
    presentation_units: dict[str, frozenset[str]] = {}
    for effect_id, entries in sorted(effects.items()):
        if len(entries) > 1:
            witnesses.append(
                Witness(
                    "conflicting-equivalence",
                    "effect-equivocation",
                    tuple(sorted(text for text, _ in entries)[:2]),
                    (),
                    f"effect {effect_id} has conflicting observations",
                )
            )
            continue
        text, fact = entries[0]
        valid_effects[effect_id] = (text, fact)
        entry = valid_presentations.get(fact["presentation"])
        if entry is None:
            witnesses.append(
                Witness(
                    "unresolved-effect",
                    "effect-missing-presentation",
                    (text,),
                    (f"presentation:{fact['presentation']}",),
                    f"effect {effect_id} refers to an unobserved presentation",
                )
            )
            continue
        presentation_id = fact["presentation"]
        atoms = presentation_units.get(presentation_id)
        if atoms is None:
            atoms = frozenset(unit for unit, _ in entry[1]["atoms"])
            presentation_units[presentation_id] = atoms
        if fact["unit"] not in atoms:
            witnesses.append(
                Witness(
                    "unresolved-effect",
                    "effect-unit-not-in-presentation",
                    tuple(sorted((text, entry[0]))),
                    (),
                    f"effect {effect_id} does not attach to a unit in its presentation",
                )
            )
        if fact["unit"] not in mints:
            witnesses.append(
                Witness(
                    "unresolved-effect",
                    "effect-missing-mint",
                    (text,),
                    (f"mint:{fact['unit']}",),
                    f"effect {effect_id} refers to an unobserved unit",
                )
            )

    unique_witnesses: dict[tuple[Any, ...], Witness] = {}
    for witness in witnesses:
        key = (witness.category, witness.code, witness.facts, witness.missing)
        unique_witnesses.setdefault(key, witness)
    ordered = tuple(
        sorted(
            unique_witnesses.values(),
            key=lambda item: (item.category, item.code, item.facts, item.missing),
        )
    )

    gross_mass = sum(fact["mass"] for _, fact in valid_mints.values())
    passing = sum(1 for _, fact in valid_effects.values() if fact["outcome"] == "pass")
    failing = sum(1 for _, fact in valid_effects.values() if fact["outcome"] == "fail")
    return DecodeResult(
        determined=not ordered,
        unique_units=len(valid_mints),
        gross_mass=gross_mass,
        presentations=len(valid_presentations),
        effects=len(valid_effects),
        passing_effects=passing,
        failing_effects=failing,
        witnesses=ordered,
    )


def transform_law_holds(transform: Fact, presentations: dict[str, Fact]) -> bool:
    try:
        inputs = [presentations[value] for value in transform["inputs"]]
        outputs = [presentations[value] for value in transform["outputs"]]
    except KeyError:
        return False
    expected = _expected_transform(transform, inputs)
    actual = [_atom_counter(item) for item in outputs]
    return bool(expected) and len(expected) == len(actual) and all(
        left == right for left, right in zip(expected, actual)
    )
