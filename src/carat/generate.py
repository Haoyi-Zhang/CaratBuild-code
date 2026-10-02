"""Deterministic synthetic history and ambiguity-family generation."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Iterable

from .facts import Fact, effect, event_id, mint, presentation, transform


@dataclass
class EventFactory:
    origins: tuple[str, ...]

    def __post_init__(self) -> None:
        self._next = {origin: 1 for origin in self.origins}

    def take(self, origin: str) -> str:
        sequence = self._next[origin]
        self._next[origin] = sequence + 1
        return event_id(origin, sequence)


@dataclass(frozen=True)
class Scenario:
    facts: tuple[Fact, ...]
    oracle_mass: int
    oracle_units: int
    base_presentations: int
    all_presentations: int


def partition_units(units: list[str], groups: int) -> list[list[str]]:
    groups = max(1, min(groups, len(units)))
    result: list[list[str]] = [[] for _ in range(groups)]
    for index, unit in enumerate(units):
        result[index % groups].append(unit)
    return [value for value in result if value]


def masses_from_profile(changed_lines: int, files_changed: int, salt: int) -> list[int]:
    rng = random.Random(1009 + salt * 7919)
    changed_lines = max(4, changed_lines)
    # Each work unit carries a positive integer mass, so the number of units
    # cannot exceed the total changed-line mass for tiny public profiles.
    desired = min(64, max(4, files_changed * 3, changed_lines // 4))
    unit_count = min(changed_lines, desired)
    weights = [1 + rng.randrange(5) for _ in range(unit_count)]
    total = sum(weights)
    masses = [max(1, round(changed_lines * value / total)) for value in weights]
    difference = changed_lines - sum(masses)
    cursor = 0
    while difference:
        index = cursor % len(masses)
        if difference > 0:
            masses[index] += 1
            difference -= 1
        elif masses[index] > 1:
            masses[index] -= 1
            difference += 1
        cursor += 1
    return masses


def amplification_history(
    masses: Iterable[int], copies: int, operation: str, salt: int = 0
) -> Scenario:
    """Generate repeated declared presentations without inventing new work units.

    Fork uses one certificate with all outputs. Squash uses two disjoint input
    presentations for every certificate. The remaining operations use their
    natural one-input/one-output shapes.
    """

    if operation not in {"rebase", "cherry_pick", "fork", "squash", "revert"}:
        raise ValueError("unknown operation")
    if type(copies) is not int or copies < 0:
        raise ValueError("copies must be a non-negative integer")
    masses_list = list(masses)
    if not masses_list or any(type(value) is not int or value < 1 for value in masses_list):
        raise ValueError("masses must be positive integers")
    if operation == "squash" and len(masses_list) < 2:
        raise ValueError("squash amplification requires at least two units")

    origins = ("n0", "n1", "n2", "n3", "n4")
    factory = EventFactory(origins)
    facts: list[Fact] = []
    batch = f"amp-{salt}"
    units = [f"u{salt}-{index}" for index in range(len(masses_list))]
    for index, (unit, mass) in enumerate(zip(units, masses_list)):
        origin = origins[index % len(origins)]
        facts.append(mint(factory.take(origin), unit, mass, origin, batch))

    base_presentations: list[str] = []
    base_for_unit: dict[str, str] = {}
    if operation == "squash":
        midpoint = (len(units) + 1) // 2
        groups = (units[:midpoint], units[midpoint:])
        for index, group in enumerate(groups):
            base = f"p{salt}-base-{index}"
            base_presentations.append(base)
            for unit in group:
                base_for_unit[unit] = base
            facts.append(
                presentation(
                    factory.take(origins[index]),
                    base,
                    [(unit, 1) for unit in group],
                    "main",
                    0,
                    batch,
                )
            )
    else:
        base = f"p{salt}-base"
        base_presentations.append(base)
        base_for_unit.update({unit: base for unit in units})
        facts.append(
            presentation(
                factory.take("n0"),
                base,
                [(unit, 1) for unit in units],
                "main",
                0,
                batch,
            )
        )

    created_presentations = len(base_presentations)
    if operation == "fork" and copies:
        outputs: list[str] = []
        for index in range(copies):
            origin = origins[(index + 1) % len(origins)]
            output = f"p{salt}-fork-{index}"
            outputs.append(output)
            facts.append(
                presentation(
                    factory.take(origin),
                    output,
                    [(unit, 1) for unit in units],
                    f"fork-{index}",
                    1,
                    batch,
                )
            )
        facts.append(
            transform(
                factory.take("n1"),
                f"t{salt}-fork",
                "fork",
                base_presentations,
                outputs,
                [],
                batch,
            )
        )
        created_presentations += copies
    elif operation == "squash":
        for index in range(copies):
            origin = origins[(index + 2) % len(origins)]
            output = f"p{salt}-squash-{index}"
            facts.append(
                presentation(
                    factory.take(origin),
                    output,
                    [(unit, 1) for unit in units],
                    f"squash-{index}",
                    1,
                    batch,
                )
            )
            facts.append(
                transform(
                    factory.take(origin),
                    f"t{salt}-squash-{index}",
                    "squash",
                    base_presentations,
                    [output],
                    [],
                    batch,
                )
            )
            created_presentations += 1
    else:
        current = base_presentations[0]
        for index in range(copies):
            origin = origins[(index + 1) % len(origins)]
            output = f"p{salt}-{operation}-{index}"
            if operation == "revert":
                atoms = [(unit, -1) for unit in units]
                branch = f"revert-{index}"
                input_presentation = base_presentations[0]
            else:
                atoms = [(unit, 1) for unit in units]
                branch = f"branch-{index % 3}"
                input_presentation = current if operation == "rebase" else base_presentations[0]
            facts.append(
                presentation(
                    factory.take(origin), output, atoms, branch, index + 1, batch
                )
            )
            facts.append(
                transform(
                    factory.take(origin),
                    f"t{salt}-{operation}-{index}",
                    operation,
                    [input_presentation],
                    [output],
                    [],
                    batch,
                )
            )
            created_presentations += 1
            if operation == "rebase":
                current = output

    selected_units = units[:: max(1, len(units) // 8)]
    for index, unit in enumerate(selected_units):
        facts.append(
            effect(
                factory.take(origins[index % len(origins)]),
                f"e{salt}-{index}",
                base_for_unit[unit],
                unit,
                "fail" if index % 7 == 0 else "pass",
                f"dep-{index % 4}",
                batch,
            )
        )
    return Scenario(
        facts=tuple(facts),
        oracle_mass=sum(masses_list),
        oracle_units=len(units),
        base_presentations=len(base_presentations),
        all_presentations=created_presentations,
    )


def operation_sequence_history(
    operations: Iterable[str], masses: Iterable[int] = (2, 3, 5), salt: int = 0
) -> Scenario:
    """Generate one genuinely composed transformation sequence.

    Every operation consumes the presentation produced by its predecessor.  This
    differs from the amplification family, where repeated fork/cherry-pick/revert
    presentations may intentionally share an earlier source to model duplicate
    appearances.
    """

    operation_list = list(operations)
    allowed = {"rebase", "cherry_pick", "revert"}
    if not operation_list or any(operation not in allowed for operation in operation_list):
        raise ValueError("operation sequence must contain rebase, cherry_pick, or revert")

    origins = ("n0", "n1", "n2", "n3", "n4")
    factory = EventFactory(origins)
    batch = f"seq-{salt}"
    masses_list = list(masses)
    if not masses_list or any(value < 1 for value in masses_list):
        raise ValueError("operation sequence masses must be positive")

    units = [f"s{salt}-u{index}" for index in range(len(masses_list))]
    facts: list[Fact] = []
    for index, (unit, mass) in enumerate(zip(units, masses_list)):
        origin = origins[index % len(origins)]
        facts.append(mint(factory.take(origin), unit, mass, origin, batch))

    current = f"s{salt}-base"
    current_atoms = [(unit, 1) for unit in units]
    facts.append(
        presentation(factory.take("n0"), current, current_atoms, "main", 0, batch)
    )
    for index, operation in enumerate(operation_list):
        origin = origins[(index + 1) % len(origins)]
        output = f"s{salt}-{index}-{operation}"
        output_atoms = (
            [(unit, -sign) for unit, sign in current_atoms]
            if operation == "revert"
            else list(current_atoms)
        )
        facts.append(
            presentation(
                factory.take(origin),
                output,
                output_atoms,
                f"sequence-{index}",
                index + 1,
                batch,
            )
        )
        facts.append(
            transform(
                factory.take(origin),
                f"s{salt}-transform-{index}",
                operation,
                [current],
                [output],
                [],
                batch,
            )
        )
        current = output
        current_atoms = output_atoms

    return Scenario(
        facts=tuple(facts),
        oracle_mass=sum(masses_list),
        oracle_units=len(units),
        base_presentations=1,
        all_presentations=1 + len(operation_list),
    )


def mixed_history(
    changed_lines: int,
    files_changed: int,
    history_index: int,
    rewrite_rounds: int = 2,
    include_revert: bool = True,
) -> Scenario:
    origins = ("n0", "n1", "n2", "n3", "n4")
    factory = EventFactory(origins)
    rng = random.Random(2221 + history_index * 17)
    batch = f"hist-{history_index}"
    masses = masses_from_profile(changed_lines, files_changed, history_index)
    units = [f"h{history_index}-u{index}" for index in range(len(masses))]
    facts: list[Fact] = []
    for index, (unit, mass) in enumerate(zip(units, masses)):
        origin = origins[index % len(origins)]
        facts.append(mint(factory.take(origin), unit, mass, origin, batch))

    groups = partition_units(units, max(2, min(8, files_changed + 1)))
    base_presentations: list[str] = []
    for index, group in enumerate(groups):
        pid = f"h{history_index}-base-{index}"
        base_presentations.append(pid)
        origin = origins[index % len(origins)]
        facts.append(
            presentation(
                factory.take(origin),
                pid,
                [(unit, 1) for unit in group],
                "main",
                0,
                batch,
            )
        )

    current = list(base_presentations)
    presentation_count = len(current)
    for round_index in range(rewrite_rounds):
        rewritten: list[str] = []
        for index, input_pid in enumerate(current):
            source_fact = next(
                fact
                for fact in facts
                if fact["kind"] == "presentation" and fact["presentation"] == input_pid
            )
            pid = f"h{history_index}-reb-{round_index}-{index}"
            origin = origins[(round_index + index + 1) % len(origins)]
            facts.append(
                presentation(
                    factory.take(origin),
                    pid,
                    [(unit, int(sign)) for unit, sign in source_fact["atoms"]],
                    f"fork-{round_index % 2}",
                    round_index + 1,
                    batch,
                )
            )
            facts.append(
                transform(
                    factory.take(origin),
                    f"h{history_index}-tr-reb-{round_index}-{index}",
                    "rebase",
                    [input_pid],
                    [pid],
                    [],
                    batch,
                )
            )
            rewritten.append(pid)
            presentation_count += 1
        current = rewritten

    cherry_outputs: list[str] = []
    for index, input_pid in enumerate(current[::2]):
        source_fact = next(
            fact
            for fact in facts
            if fact["kind"] == "presentation" and fact["presentation"] == input_pid
        )
        pid = f"h{history_index}-cherry-{index}"
        origin = origins[(index + 2) % len(origins)]
        facts.append(
            presentation(
                factory.take(origin),
                pid,
                [(unit, int(sign)) for unit, sign in source_fact["atoms"]],
                "release",
                rewrite_rounds + 1,
                batch,
            )
        )
        facts.append(
            transform(
                factory.take(origin),
                f"h{history_index}-tr-cherry-{index}",
                "cherry_pick",
                [input_pid],
                [pid],
                [],
                batch,
            )
        )
        cherry_outputs.append(pid)
        presentation_count += 1

    if len(current) > 1:
        atom_union: dict[str, int] = {}
        for input_pid in current:
            source_fact = next(
                fact
                for fact in facts
                if fact["kind"] == "presentation" and fact["presentation"] == input_pid
            )
            atom_union.update({unit: int(sign) for unit, sign in source_fact["atoms"]})
        squash_pid = f"h{history_index}-squash"
        facts.append(
            presentation(
                factory.take("n3"),
                squash_pid,
                sorted(atom_union.items()),
                "compact",
                rewrite_rounds + 2,
                batch,
            )
        )
        facts.append(
            transform(
                factory.take("n3"),
                f"h{history_index}-tr-squash",
                "squash",
                current,
                [squash_pid],
                [],
                batch,
            )
        )
        presentation_count += 1
        current = [squash_pid]

    if include_revert and current:
        target = current[0]
        source_fact = next(
            fact
            for fact in facts
            if fact["kind"] == "presentation" and fact["presentation"] == target
        )
        revert_pid = f"h{history_index}-revert"
        facts.append(
            presentation(
                factory.take("n4"),
                revert_pid,
                [(unit, -int(sign)) for unit, sign in source_fact["atoms"]],
                "rollback",
                rewrite_rounds + 3,
                batch,
            )
        )
        facts.append(
            transform(
                factory.take("n4"),
                f"h{history_index}-tr-revert",
                "revert",
                [target],
                [revert_pid],
                [],
                batch,
            )
        )
        presentation_count += 1

    candidates = base_presentations + cherry_outputs
    for index, unit in enumerate(rng.sample(units, k=min(12, len(units)))):
        containing = next(
            pid
            for pid in candidates
            if unit
            in {
                atom_unit
                for atom_unit, _ in next(
                    fact
                    for fact in facts
                    if fact["kind"] == "presentation" and fact["presentation"] == pid
                )["atoms"]
            }
        )
        origin = origins[(index + 1) % len(origins)]
        facts.append(
            effect(
                factory.take(origin),
                f"h{history_index}-effect-{index}",
                containing,
                unit,
                "fail" if index % 9 == 0 else "pass",
                f"dependency-{index % max(1, files_changed)}",
                batch,
            )
        )

    return Scenario(
        facts=tuple(facts),
        oracle_mass=sum(masses),
        oracle_units=len(units),
        base_presentations=len(base_presentations),
        all_presentations=presentation_count,
    )


def ambiguity_variant(scenario: Scenario, variant: str, salt: int) -> tuple[Fact, ...]:
    facts = [dict(fact) for fact in scenario.facts]
    if variant == "missing-transform-output":
        transform_fact = next(fact for fact in facts if fact["kind"] == "transform")
        removed_id = transform_fact["outputs"][0]
        index = next(
            i
            for i, fact in enumerate(facts)
            if fact["kind"] == "presentation" and fact["presentation"] == removed_id
        )
        del facts[index]
        if not any(
            fact["kind"] == "transform" and removed_id in fact["outputs"]
            for fact in facts
        ):
            raise RuntimeError("missing-output variant lost its referencing transform")
    elif variant == "conflicting-presentation":
        target = next(fact for fact in facts if fact["kind"] == "presentation")
        altered = dict(target)
        altered["event"] = f"n4:{900000 + salt}"
        altered["atoms"] = [list(atom) for atom in target["atoms"]]
        altered["atoms"][0][1] *= -1
        facts.append(altered)
    elif variant == "unresolved-effect":
        target = next(fact for fact in facts if fact["kind"] == "effect")
        target["presentation"] = f"missing-presentation-{salt}"
    elif variant == "conflicting-event":
        target = next(fact for fact in facts if fact["kind"] == "effect")
        altered = dict(target)
        altered["outcome"] = "fail" if target["outcome"] != "fail" else "pass"
        facts.append(altered)
    elif variant == "invalid-transform":
        target = next(fact for fact in facts if fact["kind"] == "presentation" and "reb" in fact["presentation"])
        target["atoms"] = [list(atom) for atom in target["atoms"]]
        target["atoms"][0][1] *= -1
    else:
        raise ValueError(f"unknown ambiguity variant: {variant}")
    return tuple(facts)


def recoordinate(facts: Iterable[Fact]) -> tuple[Fact, ...]:
    """Assign consecutive per-origin event coordinates across combined batches."""
    counters: dict[str, int] = {}
    output: list[Fact] = []
    for original in facts:
        fact = dict(original)
        origin = str(fact["event"]).rsplit(":", 1)[0]
        counters[origin] = counters.get(origin, 0) + 1
        fact["event"] = event_id(origin, counters[origin])
        output.append(fact)
    return tuple(output)
