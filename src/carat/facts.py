"""Atomic fact construction and deterministic wire normalization for CARAT."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

Fact = dict[str, Any]

_EVENT_SEQUENCE = re.compile(r"[1-9][0-9]*\Z")
_ALLOWED_OPERATIONS = {"rebase", "cherry_pick", "squash", "fork", "revert"}
_ALLOWED_OUTCOMES = {"pass", "fail", "skip"}

# A seal is a bounded declaration, not an instruction to expand an arbitrary
# integer interval.  The largest retained generated campaign uses 20,000 data
# coordinates, so this leaves substantial headroom while rejecting malformed
# billion-coordinate declarations before either checker constructs diagnostics.
MAX_SEAL_SPAN = 300_000


class FactError(ValueError):
    """Raised when a fact does not match the public schema."""


def _nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise FactError(f"field {field!r} must be a non-empty string")
    return value


def event_id(origin: str, sequence: int) -> str:
    origin = _nonempty_string(origin, "origin")
    if ":" in origin or type(sequence) is not int or sequence < 1:
        raise FactError("invalid event coordinate")
    return f"{origin}:{sequence}"


def split_event(value: str) -> tuple[str, int]:
    if type(value) is not str:
        raise FactError(f"invalid event coordinate: {value!r}")
    try:
        origin, raw = value.rsplit(":", 1)
    except ValueError as exc:
        raise FactError(f"invalid event coordinate: {value!r}") from exc
    if not origin or ":" in origin or _EVENT_SEQUENCE.fullmatch(raw) is None:
        raise FactError(f"invalid event coordinate: {value!r}")
    return origin, int(raw)


def _base(event: str, kind: str) -> Fact:
    split_event(event)
    return {"event": event, "kind": kind}


def mint(event: str, unit: str, mass: int, source: str, batch: str) -> Fact:
    unit = _nonempty_string(unit, "unit")
    source = _nonempty_string(source, "source")
    batch = _nonempty_string(batch, "batch")
    origin, _ = split_event(event)
    if type(mass) is not int or mass < 1 or source != origin:
        raise FactError("invalid mint fact")
    value = _base(event, "mint")
    value.update(unit=unit, mass=mass, source=source, batch=batch)
    return value


def presentation(
    event: str,
    presentation_id: str,
    atoms: Iterable[tuple[str, int] | list[Any]],
    branch: str,
    generation: int,
    batch: str,
) -> Fact:
    presentation_id = _nonempty_string(presentation_id, "presentation")
    branch = _nonempty_string(branch, "branch")
    batch = _nonempty_string(batch, "batch")
    if type(generation) is not int or generation < 0:
        raise FactError("invalid presentation fact")

    normalized: list[list[Any]] = []
    seen: set[str] = set()
    try:
        iterator = iter(atoms)
    except TypeError as exc:
        raise FactError("invalid presentation atoms") from exc
    for atom in iterator:
        if not isinstance(atom, (tuple, list)) or len(atom) != 2:
            raise FactError("invalid presentation atom")
        unit = _nonempty_string(atom[0], "atom unit")
        sign = atom[1]
        if type(sign) is not int or sign not in (-1, 1) or unit in seen:
            raise FactError("invalid presentation atom")
        seen.add(unit)
        normalized.append([unit, sign])
    if not normalized:
        raise FactError("a presentation must contain at least one atom")
    normalized.sort(key=lambda item: item[0])

    value = _base(event, "presentation")
    value.update(
        presentation=presentation_id,
        atoms=normalized,
        branch=branch,
        generation=generation,
        batch=batch,
    )
    return value


def _string_list(values: Iterable[str], field: str, *, nonempty: bool) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise FactError(f"invalid {field} references")
    try:
        result = [_nonempty_string(value, field) for value in values]
    except TypeError as exc:
        raise FactError(f"invalid {field} references") from exc
    if nonempty and not result:
        raise FactError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise FactError(f"duplicate {field} reference")
    return sorted(result)


def _validate_transform_contract(
    operation: str,
    inputs: list[str],
    outputs: list[str],
    fresh: list[str],
) -> None:
    """Enforce the finite transformation algebra used by both checkers.

    ``fresh`` is retained on the wire for schema stability, but the current
    algebra does not mint through transformations: only a ``mint`` fact may
    create a unit.  Consequently it must be empty.  This removes the previous
    ambiguity where a transformation could add an already-minted positive unit.
    """

    if operation not in _ALLOWED_OPERATIONS:
        raise FactError("invalid transform operation")
    if fresh:
        raise FactError("transform fresh must be empty; only mint creates units")
    if not set(inputs).isdisjoint(outputs):
        raise FactError("transform input and output identifiers must be disjoint")
    if operation in {"rebase", "cherry_pick", "revert"}:
        if len(inputs) != 1 or len(outputs) != 1:
            raise FactError(f"{operation} requires exactly one input and one output")
    elif operation == "fork":
        if len(inputs) != 1 or not outputs:
            raise FactError("fork requires exactly one input and at least one output")
    elif operation == "squash":
        if len(inputs) < 2 or len(outputs) != 1:
            raise FactError("squash requires at least two inputs and exactly one output")


def transform(
    event: str,
    transform_id: str,
    operation: str,
    inputs: Iterable[str],
    outputs: Iterable[str],
    fresh: Iterable[str],
    batch: str,
) -> Fact:
    transform_id = _nonempty_string(transform_id, "transform")
    operation = _nonempty_string(operation, "operation")
    batch = _nonempty_string(batch, "batch")
    input_list = _string_list(inputs, "input", nonempty=True)
    output_list = _string_list(outputs, "output", nonempty=True)
    fresh_list = _string_list(fresh, "fresh", nonempty=False)
    _validate_transform_contract(operation, input_list, output_list, fresh_list)

    value = _base(event, "transform")
    value.update(
        transform=transform_id,
        operation=operation,
        inputs=input_list,
        outputs=output_list,
        fresh=fresh_list,
        batch=batch,
    )
    return value


def seal(
    event: str,
    seal_id: str,
    batch: str,
    origin: str,
    previous: int,
    frontier: int,
) -> Fact:
    """Declare one origin's complete coordinate interval for a batch.

    A seal is valid only at ``frontier + 1`` for its own origin.  The closure
    checker, rather than this shape constructor, verifies that every coordinate
    in ``(previous, frontier]`` is present and belongs to ``batch``.
    """

    seal_id = _nonempty_string(seal_id, "seal")
    batch = _nonempty_string(batch, "batch")
    origin = _nonempty_string(origin, "origin")
    event_origin, sequence = split_event(event)
    if (
        event_origin != origin
        or type(previous) is not int
        or type(frontier) is not int
        or previous < 0
        or frontier < previous
    ):
        raise FactError("invalid seal fact")
    if frontier - previous > MAX_SEAL_SPAN:
        raise FactError("seal interval span exceeds bound")
    if sequence != frontier + 1:
        raise FactError("invalid seal fact")
    value = _base(event, "seal")
    value.update(
        seal=seal_id,
        batch=batch,
        origin=origin,
        previous=previous,
        frontier=frontier,
    )
    return value


def effect(
    event: str,
    effect_id: str,
    presentation_id: str,
    unit: str,
    outcome: str,
    dependency: str,
    batch: str,
) -> Fact:
    effect_id = _nonempty_string(effect_id, "effect")
    presentation_id = _nonempty_string(presentation_id, "presentation")
    unit = _nonempty_string(unit, "unit")
    outcome = _nonempty_string(outcome, "outcome")
    dependency = _nonempty_string(dependency, "dependency")
    batch = _nonempty_string(batch, "batch")
    if outcome not in _ALLOWED_OUTCOMES:
        raise FactError("invalid effect fact")
    value = _base(event, "effect")
    value.update(
        effect=effect_id,
        presentation=presentation_id,
        unit=unit,
        outcome=outcome,
        dependency=dependency,
        batch=batch,
    )
    return value


def validate_shape(fact: Any) -> None:
    if type(fact) is not dict:
        raise FactError("fact must be an object")
    kind = fact.get("kind")
    event = fact.get("event")
    split_event(event)

    if kind == "mint":
        required = {"event", "kind", "unit", "mass", "source", "batch"}
        if set(fact) != required:
            raise FactError("malformed mint fact")
        _nonempty_string(fact["unit"], "unit")
        source = _nonempty_string(fact["source"], "source")
        _nonempty_string(fact["batch"], "batch")
        if type(fact["mass"]) is not int or fact["mass"] < 1:
            raise FactError("malformed mint fact")
        origin, _ = split_event(event)
        if source != origin:
            raise FactError("mint source must equal event origin")
        return

    if kind == "presentation":
        required = {"event", "kind", "presentation", "atoms", "branch", "generation", "batch"}
        if set(fact) != required:
            raise FactError("malformed presentation fact")
        _nonempty_string(fact["presentation"], "presentation")
        _nonempty_string(fact["branch"], "branch")
        _nonempty_string(fact["batch"], "batch")
        if type(fact["generation"]) is not int or fact["generation"] < 0:
            raise FactError("malformed presentation fact")
        atoms = fact["atoms"]
        if type(atoms) is not list or not atoms:
            raise FactError("malformed presentation atoms")
        units: set[str] = set()
        for atom in atoms:
            if type(atom) is not list or len(atom) != 2:
                raise FactError("malformed presentation atom")
            unit = _nonempty_string(atom[0], "atom unit")
            sign = atom[1]
            if type(sign) is not int or sign not in (-1, 1) or unit in units:
                raise FactError("malformed presentation atom")
            units.add(unit)
        return

    if kind == "transform":
        required = {
            "event",
            "kind",
            "transform",
            "operation",
            "inputs",
            "outputs",
            "fresh",
            "batch",
        }
        if set(fact) != required:
            raise FactError("malformed transform fact")
        _nonempty_string(fact["transform"], "transform")
        operation = _nonempty_string(fact["operation"], "operation")
        _nonempty_string(fact["batch"], "batch")
        for name, nonempty in (("inputs", True), ("outputs", True), ("fresh", False)):
            values = fact[name]
            if type(values) is not list or (nonempty and not values):
                raise FactError("malformed transform references")
            parsed = [_nonempty_string(value, name) for value in values]
            if len(parsed) != len(set(parsed)):
                raise FactError("duplicate transform reference")
        _validate_transform_contract(
            operation,
            list(fact["inputs"]),
            list(fact["outputs"]),
            list(fact["fresh"]),
        )
        return

    if kind == "seal":
        required = {
            "event",
            "kind",
            "seal",
            "batch",
            "origin",
            "previous",
            "frontier",
        }
        if set(fact) != required:
            raise FactError("malformed seal fact")
        _nonempty_string(fact["seal"], "seal")
        _nonempty_string(fact["batch"], "batch")
        origin = _nonempty_string(fact["origin"], "origin")
        previous = fact["previous"]
        frontier = fact["frontier"]
        event_origin, sequence = split_event(event)
        if (
            event_origin != origin
            or type(previous) is not int
            or type(frontier) is not int
            or previous < 0
            or frontier < previous
        ):
            raise FactError("malformed seal fact")
        if frontier - previous > MAX_SEAL_SPAN:
            raise FactError("seal interval span exceeds bound")
        if sequence != frontier + 1:
            raise FactError("malformed seal fact")
        return

    if kind == "effect":
        required = {
            "event",
            "kind",
            "effect",
            "presentation",
            "unit",
            "outcome",
            "dependency",
            "batch",
        }
        if set(fact) != required:
            raise FactError("malformed effect fact")
        for field in ("effect", "presentation", "unit", "dependency", "batch"):
            _nonempty_string(fact[field], field)
        outcome = _nonempty_string(fact["outcome"], "outcome")
        if outcome not in _ALLOWED_OUTCOMES:
            raise FactError("malformed effect fact")
        return

    raise FactError(f"unknown fact kind: {kind!r}")


def normalize_fact(fact: Fact) -> Fact:
    """Validate and return the unique normalized representation of a fact."""

    validate_shape(fact)
    event = event_id(*split_event(fact["event"]))
    kind = fact["kind"]
    if kind == "mint":
        return mint(event, fact["unit"], fact["mass"], fact["source"], fact["batch"])
    if kind == "presentation":
        return presentation(
            event,
            fact["presentation"],
            ((atom[0], atom[1]) for atom in fact["atoms"]),
            fact["branch"],
            fact["generation"],
            fact["batch"],
        )
    if kind == "transform":
        return transform(
            event,
            fact["transform"],
            fact["operation"],
            fact["inputs"],
            fact["outputs"],
            fact["fresh"],
            fact["batch"],
        )
    if kind == "seal":
        return seal(
            event,
            fact["seal"],
            fact["batch"],
            fact["origin"],
            fact["previous"],
            fact["frontier"],
        )
    if kind == "effect":
        return effect(
            event,
            fact["effect"],
            fact["presentation"],
            fact["unit"],
            fact["outcome"],
            fact["dependency"],
            fact["batch"],
        )
    raise FactError(f"unknown fact kind: {kind!r}")


def _encode_normalized(fact: Fact) -> str:
    return json.dumps(fact, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_text(fact: Fact) -> str:
    return _encode_normalized(normalize_fact(fact))


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FactError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise FactError(f"non-finite JSON number is forbidden: {value}")


def parse_text(text: str) -> Fact:
    if type(text) is not str:
        raise FactError("fact text must be a string")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except FactError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise FactError("fact text is not valid JSON") from exc
    return normalize_fact(value)


def canonical_pair(value: str | Fact) -> tuple[str, Fact]:
    fact = parse_text(value) if isinstance(value, str) else normalize_fact(value)
    return _encode_normalized(fact), fact


def canonicalize_text(text: str) -> str:
    return canonical_pair(text)[0]
