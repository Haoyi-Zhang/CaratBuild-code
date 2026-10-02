"""Join-based replica state and bounded interval anti-entropy for CARAT."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Iterable

from .facts import Fact, canonical_pair, canonical_text, parse_text, split_event


@dataclass(frozen=True)
class OriginSummary:
    frontier: int
    maximum: int
    missing: tuple[tuple[int, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "frontier": self.frontier,
            "maximum": self.maximum,
            "missing": [list(pair) for pair in self.missing],
        }


@dataclass
class Replica:
    """A grow-only set of normalized facts. Merge is set union."""

    name: str
    facts: set[str] = field(default_factory=set)
    next_sequence: int = 1
    arrival_order: list[str] = field(default_factory=list)
    _coordinate_by_text: dict[str, tuple[str, int]] = field(default_factory=dict, repr=False)
    _texts_by_origin_sequence: dict[str, dict[int, set[str]]] = field(
        default_factory=dict, repr=False
    )

    def append(self, fact: Fact) -> bool:
        return self._add_text(canonical_text(fact))

    def append_text(self, text: str) -> bool:
        return self._add_text(text)

    def _add_text(self, text: str) -> bool:
        canonical, fact = canonical_pair(text)
        if canonical in self.facts:
            return False
        coordinate = split_event(fact["event"])
        origin, sequence = coordinate
        if origin == self.name:
            self.next_sequence = max(self.next_sequence, sequence + 1)
        self.facts.add(canonical)
        self.arrival_order.append(canonical)
        self._coordinate_by_text[canonical] = coordinate
        self._texts_by_origin_sequence.setdefault(origin, {}).setdefault(sequence, set()).add(
            canonical
        )
        return True

    def merge(self, other: "Replica") -> int:
        return sum(int(self._add_text(text)) for text in sorted(other.facts))

    def summary(self, interval_limit: int = 64) -> dict[str, OriginSummary]:
        if type(interval_limit) is not int or interval_limit < 1:
            raise ValueError("interval_limit must be a positive integer")
        result: dict[str, OriginSummary] = {}
        for origin in sorted(self._texts_by_origin_sequence):
            occupied = sorted(self._texts_by_origin_sequence[origin])
            maximum = occupied[-1]
            frontier = 0
            for sequence in occupied:
                if sequence == frontier + 1:
                    frontier = sequence
                elif sequence > frontier + 1:
                    break

            intervals: list[tuple[int, int]] = []
            previous = frontier
            for sequence in occupied:
                if sequence <= frontier:
                    continue
                if sequence > previous + 1:
                    intervals.append((previous + 1, sequence - 1))
                    if len(intervals) >= interval_limit:
                        break
                previous = sequence
            result[origin] = OriginSummary(frontier, maximum, tuple(intervals))
        return result

    def delta_for(
        self, peer: dict[str, OriginSummary], batch_limit: int = 4096
    ) -> list[str]:
        if type(batch_limit) is not int or batch_limit < 1:
            raise ValueError("batch_limit must be a positive integer")
        candidates: list[str] = []
        for origin in sorted(self._texts_by_origin_sequence):
            peer_item = peer.get(origin)
            missing = peer_item.missing if peer_item is not None else ()
            for sequence in sorted(self._texts_by_origin_sequence[origin]):
                needed = peer_item is None or sequence > peer_item.maximum
                if peer_item is not None and not needed:
                    needed = any(start <= sequence <= end for start, end in missing)
                if not needed:
                    continue
                for text in sorted(self._texts_by_origin_sequence[origin][sequence]):
                    candidates.append(text)
                    if len(candidates) >= batch_limit:
                        return candidates
        return candidates

    def serialized_bytes(self) -> int:
        return sum(len(text.encode("utf-8")) + 1 for text in self.facts)

    def summary_bytes(self) -> int:
        payload = {origin: summary.as_dict() for origin, summary in self.summary().items()}
        return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def facts_as_dicts(self) -> list[Fact]:
        return [parse_text(text) for text in sorted(self.facts)]


def joined_state(replicas: Iterable[Replica], name: str = "joined") -> Replica:
    output = Replica(name)
    for replica in replicas:
        output.merge(replica)
    return output


def states_equal(replicas: Iterable[Replica]) -> bool:
    values = list(replicas)
    return not values or all(replica.facts == values[0].facts for replica in values[1:])
