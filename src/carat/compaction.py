"""Closure-barrier compaction for sealed history batches."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from typing import Iterable
import zlib

from .decode import decode
from .facts import Fact, canonical_pair, canonical_text, parse_text
from .independent_check import check, check_sealed

MAX_REPLAY_INDEX_BYTES = 32 * 1024 * 1024


def _encode_replay_facts(values: Iterable[str]) -> tuple[int, str]:
    texts = tuple(sorted(set(values)))
    raw = "\n".join(texts).encode("utf-8")
    if len(raw) > MAX_REPLAY_INDEX_BYTES:
        raise ValueError("projection replay index exceeds bound")
    blob = base64.b85encode(zlib.compress(raw, level=9)).decode("ascii")
    return len(texts), blob


def _decode_replay_facts(count: object, blob: object) -> tuple[str, ...]:
    if type(count) is not int or count < 0 or type(blob) is not str:
        raise ValueError("malformed projection replay index")
    try:
        compressed = base64.b85decode(blob.encode("ascii"))
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, MAX_REPLAY_INDEX_BYTES + 1)
        remaining = MAX_REPLAY_INDEX_BYTES + 1 - len(raw)
        if remaining <= 0:
            raise ValueError("projection replay index exceeds bound")
        raw += inflater.flush(remaining)
    except (ValueError, zlib.error, UnicodeError) as exc:
        raise ValueError("malformed projection replay index") from exc
    if inflater.unconsumed_tail or len(raw) > MAX_REPLAY_INDEX_BYTES:
        raise ValueError("projection replay index exceeds bound")
    if not raw:
        texts: tuple[str, ...] = ()
    else:
        try:
            texts = tuple(raw.decode("utf-8").split("\n"))
        except UnicodeError as exc:
            raise ValueError("malformed projection replay index") from exc
    if len(texts) != count or len(texts) != len(set(texts)) or tuple(sorted(texts)) != texts:
        raise ValueError("projection replay index count or order mismatch")
    for text in texts:
        if canonical_text(parse_text(text)) != text:
            raise ValueError("projection replay index contains noncanonical fact")
    return texts


@dataclass(frozen=True)
class BatchSummary:
    batch: str
    units: tuple[tuple[str, int], ...]
    presentation_ids: tuple[str, ...]
    transform_ids: tuple[str, ...]
    effects: tuple[tuple[str, str, int], ...]
    effect_ids: tuple[str, ...]
    replay_facts: tuple[str, ...] = ()
    seal_anchors: tuple[str, ...] = ()

    @property
    def presentations(self) -> int:
        return len(self.presentation_ids)

    def as_dict(self) -> dict[str, object]:
        replay_count, replay_blob = _encode_replay_facts(self.replay_facts)
        return {
            "batch": self.batch,
            "units": [list(item) for item in self.units],
            "presentation_ids": list(self.presentation_ids),
            "transform_ids": list(self.transform_ids),
            "effects": [list(item) for item in self.effects],
            "effect_ids": list(self.effect_ids),
            "replay_count": replay_count,
            "replay_blob": replay_blob,
            "seal_anchors": list(self.seal_anchors),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "BatchSummary":
        if type(value) is not dict:
            raise ValueError("summary must be an object")
        required = {
            "batch", "units", "presentation_ids", "transform_ids",
            "effects", "effect_ids", "replay_count", "replay_blob",
            "seal_anchors",
        }
        if set(value) != required or type(value["batch"]) is not str:
            raise ValueError("malformed batch summary")
        units_raw = value["units"]
        presentation_raw = value["presentation_ids"]
        transform_raw = value["transform_ids"]
        effects_raw = value["effects"]
        effect_ids_raw = value["effect_ids"]
        seal_anchors_raw = value["seal_anchors"]
        if not all(type(item) is list for item in (
            units_raw, presentation_raw, transform_raw, effects_raw,
            effect_ids_raw, seal_anchors_raw,
        )):
            raise ValueError("malformed batch summary fields")
        replay_facts = _decode_replay_facts(value["replay_count"], value["replay_blob"])
        units: list[tuple[str, int]] = []
        for item in units_raw:
            if type(item) is not list or len(item) != 2 or type(item[0]) is not str or type(item[1]) is not int or item[1] <= 0:
                raise ValueError("malformed summarized unit")
            units.append((item[0], item[1]))
        effects: list[tuple[str, str, int]] = []
        for item in effects_raw:
            if (type(item) is not list or len(item) != 3 or type(item[0]) is not str
                    or item[1] not in {"pass", "fail", "skip"}
                    or type(item[2]) is not int or item[2] <= 0):
                raise ValueError("malformed summarized effect")
            effects.append((item[0], item[1], item[2]))
        for values, label in ((presentation_raw, "presentation"), (transform_raw, "transform"), (effect_ids_raw, "effect")):
            if any(type(item) is not str or not item for item in values) or len(values) != len(set(values)):
                raise ValueError(f"malformed summarized {label} identifiers")
        if len(units) != len({unit for unit, _mass in units}):
            raise ValueError("duplicate summarized unit")
        if any(type(item) is not str or not item for item in seal_anchors_raw):
            raise ValueError("malformed summarized seal anchors")
        summary = cls(
            batch=value["batch"],
            units=tuple(sorted(units)),
            presentation_ids=tuple(sorted(presentation_raw)),
            transform_ids=tuple(sorted(transform_raw)),
            effects=tuple(sorted(effects)),
            effect_ids=tuple(sorted(effect_ids_raw)),
            replay_facts=replay_facts,
            seal_anchors=tuple(sorted(seal_anchors_raw)),
        )
        if not _summary_contents_valid(summary):
            raise ValueError("projection summary disagrees with exact replay index")
        return summary


def _summary_contents_valid(summary: BatchSummary) -> bool:
    """Check that aggregates and fences are exactly backed by retained evidence."""

    try:
        replay = [parse_text(text) for text in summary.replay_facts]
        anchors = [parse_text(text) for text in summary.seal_anchors]
    except ValueError:
        return False
    if any(fact["kind"] == "seal" or fact.get("batch") != summary.batch for fact in replay):
        return False
    if any(fact["kind"] != "seal" or fact.get("batch") != summary.batch for fact in anchors):
        return False
    events = [fact["event"] for fact in replay + anchors]
    if len(events) != len(set(events)):
        return False
    decoded = decode(summary.replay_facts)
    checked = check(summary.replay_facts)
    if not decoded.determined or not checked.accepted:
        return False
    units = tuple(sorted(
        (fact["unit"], int(fact["mass"]))
        for fact in replay if fact["kind"] == "mint"
    ))
    presentation_ids = tuple(sorted(
        fact["presentation"] for fact in replay if fact["kind"] == "presentation"
    ))
    transform_ids = tuple(sorted(
        fact["transform"] for fact in replay if fact["kind"] == "transform"
    ))
    effect_ids = tuple(sorted(
        fact["effect"] for fact in replay if fact["kind"] == "effect"
    ))
    counts: dict[tuple[str, str], int] = {}
    for fact in replay:
        if fact["kind"] == "effect":
            key = (fact["unit"], fact["outcome"])
            counts[key] = counts.get(key, 0) + 1
    effects = tuple(sorted(
        (unit, outcome, count)
        for (unit, outcome), count in counts.items()
    ))
    return (
        summary.units == units
        and summary.presentation_ids == presentation_ids
        and summary.transform_ids == transform_ids
        and summary.effects == effects
        and summary.effect_ids == effect_ids
    )


@dataclass(frozen=True)
class CompactedLedger:
    summaries: tuple[BatchSummary, ...]
    live_facts: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "summaries": [item.as_dict() for item in self.summaries],
            "live_facts": list(self.live_facts),
        }

    def serialized_bytes(self) -> int:
        return len(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def boundary_valid(self) -> bool:
        """Validate projections against exact replay and retained seal evidence."""

        if any(not _summary_contents_valid(item) for item in self.summaries):
            return False
        batches = [item.batch for item in self.summaries]
        if len(batches) != len(set(batches)):
            return False

        closed_units = [unit for item in self.summaries for unit, _ in item.units]
        closed_presentations = [
            identifier for item in self.summaries for identifier in item.presentation_ids
        ]
        closed_transforms = [
            identifier for item in self.summaries for identifier in item.transform_ids
        ]
        closed_effects = [
            identifier for item in self.summaries for identifier in item.effect_ids
        ]
        for identifiers in (
            closed_units, closed_presentations, closed_transforms, closed_effects
        ):
            if len(identifiers) != len(set(identifiers)):
                return False

        replay_texts = {
            text for summary in self.summaries for text in summary.replay_facts
        }
        if sum(len(summary.replay_facts) for summary in self.summaries) != len(replay_texts):
            return False
        anchor_texts = {
            text for summary in self.summaries for text in summary.seal_anchors
        }
        if sum(len(summary.seal_anchors) for summary in self.summaries) != len(anchor_texts):
            return False

        replay_facts = {text: parse_text(text) for text in replay_texts}
        anchor_facts = {text: parse_text(text) for text in anchor_texts}
        reserved_events: dict[str, str] = {}
        projected_seal_ids: set[str] = set()
        for text, fact in {**replay_facts, **anchor_facts}.items():
            previous = reserved_events.get(fact["event"])
            if previous is not None and previous != text:
                return False
            reserved_events[fact["event"]] = text
            if fact["kind"] == "seal":
                if fact["seal"] in projected_seal_ids:
                    return False
                projected_seal_ids.add(fact["seal"])

        closed_set = set(batches)
        closed_identifier_sets = {
            "mint": set(closed_units),
            "presentation": set(closed_presentations),
            "transform": set(closed_transforms),
            "effect": set(closed_effects),
        }
        live_items: list[tuple[str, Fact]] = [
            (text, parse_text(text)) for text in self.live_facts
        ]
        if len(self.live_facts) != len({text for text, _fact in live_items}):
            return False

        live_seal_ids: set[str] = set()
        observed_anchors: dict[str, set[str]] = {batch: set() for batch in batches}
        for text, fact in live_items:
            expected = reserved_events.get(fact["event"])
            if expected is not None and expected != text:
                return False
            if text in replay_texts:
                return False
            if fact["kind"] == "seal":
                seal_id = fact["seal"]
                if seal_id in live_seal_ids:
                    return False
                live_seal_ids.add(seal_id)
                if seal_id in projected_seal_ids and text not in anchor_texts:
                    return False
                if fact["batch"] in closed_set:
                    if text not in anchor_texts:
                        return False
                    observed_anchors[fact["batch"]].add(text)
                continue
            if fact["batch"] in closed_set:
                return False
            field = {
                "mint": "unit",
                "presentation": "presentation",
                "transform": "transform",
                "effect": "effect",
            }[fact["kind"]]
            if fact[field] in closed_identifier_sets[fact["kind"]]:
                return False

        for summary in self.summaries:
            if observed_anchors[summary.batch] != set(summary.seal_anchors):
                return False
        return True

    def accounting(self) -> dict[str, int | bool]:
        live = decode(self.live_facts)
        # Summaries are trusted projections, not independently verifiable
        # history certificates or mergeable replica states. Fail closed on
        # obvious stale replay and identity overlap instead of double counting.
        if not live.determined or not self.boundary_valid():
            return {
                "determined": False,
                "unique_units": live.unique_units,
                "gross_mass": live.gross_mass,
                "presentations": live.presentations,
                "effects": live.effects,
                "passing_effects": live.passing_effects,
                "failing_effects": live.failing_effects,
            }
        units = live.unique_units
        mass = live.gross_mass
        presentations = live.presentations
        effects = live.effects
        passing = live.passing_effects
        failing = live.failing_effects
        for summary in self.summaries:
            units += len(summary.units)
            mass += sum(value for _, value in summary.units)
            presentations += summary.presentations
            for _unit, outcome, count in summary.effects:
                effects += count
                passing += count if outcome == "pass" else 0
                failing += count if outcome == "fail" else 0
        return {
            "determined": True,
            "unique_units": units,
            "gross_mass": mass,
            "presentations": presentations,
            "effects": effects,
            "passing_effects": passing,
            "failing_effects": failing,
        }


def compact_closed_batches(
    values: Iterable[str | Fact], closed_batches: Iterable[str]
) -> CompactedLedger:
    texts = sorted({canonical_pair(value)[0] for value in values})
    report = check(texts)
    decoded = decode(texts)
    if not report.accepted or not decoded.determined:
        raise ValueError("only determined, checker-accepted states may be compacted")
    closed = set(closed_batches)
    facts = [parse_text(text) for text in texts]

    # Every identifier dependency must stay on one side of the closure barrier.
    # These checks run before details are dropped so neither a compacted summary
    # nor the retained live state depends on a definition stored on the other side.
    unit_batch = {
        fact["unit"]: fact["batch"] for fact in facts if fact["kind"] == "mint"
    }
    presentation_batch = {
        fact["presentation"]: fact["batch"]
        for fact in facts
        if fact["kind"] == "presentation"
    }
    for fact in facts:
        fact_is_closed = fact["batch"] in closed
        if fact["kind"] == "presentation":
            reference_batches = [unit_batch[unit] for unit, _sign in fact["atoms"]]
            if any((batch in closed) != fact_is_closed for batch in reference_batches):
                raise ValueError("presentation crosses a closure barrier")
        elif fact["kind"] == "transform":
            reference_batches = [
                presentation_batch[value]
                for value in fact["inputs"] + fact["outputs"]
            ]
            if any((batch in closed) != fact_is_closed for batch in reference_batches):
                raise ValueError("transform crosses a closure barrier")
        elif fact["kind"] == "effect":
            reference_batches = [
                unit_batch[fact["unit"]],
                presentation_batch[fact["presentation"]],
            ]
            if any((batch in closed) != fact_is_closed for batch in reference_batches):
                raise ValueError("effect crosses a closure barrier")

    summaries: list[BatchSummary] = []
    for batch in sorted(closed):
        selected = [fact for fact in facts if fact["batch"] == batch]
        if not selected:
            continue
        units = tuple(
            sorted(
                (fact["unit"], int(fact["mass"]))
                for fact in selected
                if fact["kind"] == "mint"
            )
        )
        presentation_ids = tuple(sorted(
            fact["presentation"] for fact in selected if fact["kind"] == "presentation"
        ))
        transform_ids = tuple(sorted(
            fact["transform"] for fact in selected if fact["kind"] == "transform"
        ))
        effect_ids = tuple(sorted(
            fact["effect"] for fact in selected if fact["kind"] == "effect"
        ))
        effect_counts: dict[tuple[str, str], int] = {}
        for fact in selected:
            if fact["kind"] == "effect":
                key = (fact["unit"], fact["outcome"])
                effect_counts[key] = effect_counts.get(key, 0) + 1
        effects = tuple(sorted((unit, outcome, count) for (unit, outcome), count in effect_counts.items()))
        replay_facts = tuple(sorted(
            text
            for text, fact in zip(texts, facts)
            if fact["batch"] == batch and fact["kind"] != "seal"
        ))
        seal_anchors = tuple(sorted(
            text
            for text, fact in zip(texts, facts)
            if fact["batch"] == batch and fact["kind"] == "seal"
        ))
        summaries.append(BatchSummary(
            batch=batch,
            units=units,
            presentation_ids=presentation_ids,
            transform_ids=transform_ids,
            effects=effects,
            effect_ids=effect_ids,
            replay_facts=replay_facts,
            seal_anchors=seal_anchors,
        ))

    # Seal records remain as compact anchor facts.  They carry no accounting
    # mass, but the next window needs the predecessor-seal chain.
    live = tuple(
        text
        for text, fact in zip(texts, facts)
        if fact["batch"] not in closed or fact["kind"] == "seal"
    )
    ledger = CompactedLedger(tuple(summaries), live)

    # Defense in depth: even if the syntactic barrier changes later, never return
    # a ledger that loses determination or changes the declared aggregate tuple.
    after = ledger.accounting()
    aggregate_fields = (
        "unique_units",
        "gross_mass",
        "presentations",
        "effects",
        "passing_effects",
        "failing_effects",
    )
    if not after["determined"] or any(
        getattr(decoded, field) != after[field] for field in aggregate_fields
    ):
        raise ValueError("compaction changed the declared aggregate accounting")
    return ledger


def compact_sealed_batch(
    values: Iterable[str | Fact], batch: str, origins: Iterable[str]
) -> CompactedLedger:
    """Project one self-contained batch only after origin-sealed finality.

    The closure evaluator and separately structured checker must agree on the
    final result.  The projection retains the batch's seal facts as zero-mass
    anchors, so a later window can prove its predecessor chain.  This is still
    a local semantic projection: it does not prove that another replica has a
    surviving copy or authenticate an origin declaration.
    """

    texts = sorted({canonical_pair(value)[0] for value in values})
    from .closure import sealed_query

    # Both validation paths must receive the same roster, including when the
    # public Iterable API is given a generator rather than a reusable tuple.
    roster = tuple(origins)
    closed = sealed_query(texts, batch, roster)
    verified = check_sealed(texts, batch, roster)
    if (
        not closed.final
        or not verified.accepted
        or closed.closure_complete != verified.closure_complete
        or closed.unique_units != verified.unique_units
        or closed.gross_mass != verified.gross_mass
    ):
        raise ValueError("batch is not independently verified as origin-sealed")
    ledger = compact_closed_batches(texts, [batch])
    if len(ledger.summaries) != 1:
        raise ValueError("sealed projection expected exactly one batch summary")
    summary = ledger.summaries[0]
    # `ledger.accounting()` is a whole-replica aggregate and may legitimately
    # include later live batches.  Bind the projected batch result to its own
    # summary instead of confusing that global total with this window's final
    # value.  `compact_closed_batches` already checks whole-replica preservation.
    if (
        len(summary.units) != closed.unique_units
        or sum(mass for _unit, mass in summary.units) != closed.gross_mass
    ):
        raise ValueError("sealed projection changed batch accounting")
    return ledger
