"""Bounded full-fact page sweeps, an anti-entropy completeness fallback.

This is ordinary full-state transfer in bounded pages, not a new CRDT. A client
advances only after accepting a complete page. Numeric offsets are transient
scan positions, never identities or proofs that an open stream is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, Iterable, Any

from .facts import canonical_pair

MAX_PAGE_FACTS = 32
MAX_CURSOR = 300_000


def natural(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [0, {maximum}]")
    return value


def make_page(values: Iterable[str], offset: int, limit: int) -> dict[str, object]:
    natural(offset, "offset", MAX_CURSOR)
    natural(limit, "limit", MAX_PAGE_FACTS)
    if limit == 0:
        raise ValueError("limit must be positive")
    ordered = sorted(set(values))
    selected = ordered[offset:offset + limit]
    eof = offset + len(selected) >= len(ordered)
    return {"offset": offset, "next_offset": 0 if eof else offset + len(selected),
            "eof": eof, "facts": selected}


def encoded_json_bytes(value: object) -> int:
    return len(json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8"))


def make_bounded_page(
    values: Iterable[str],
    offset: int,
    limit: int,
    maximum_frame_bytes: int,
) -> dict[str, object]:
    """Return the largest count- and byte-bounded RPC response page."""

    natural(maximum_frame_bytes, "maximum_frame_bytes", 1 << 30)
    if maximum_frame_bytes == 0:
        raise ValueError("maximum frame bytes must be positive")
    page = make_page(values, offset, limit)
    while encoded_json_bytes({"ok": True, "result": page}) > maximum_frame_bytes:
        facts = page["facts"]
        if not isinstance(facts, list) or len(facts) <= 1:
            raise ValueError("one legal fact cannot fit in the configured frame")
        facts.pop()
        page["eof"] = False
        page["next_offset"] = offset + len(facts)
    return page


def split_put_batches(
    values: Iterable[str | dict[str, Any]],
    maximum_frame_bytes: int,
    maximum_facts: int = MAX_PAGE_FACTS,
) -> list[list[str]]:
    """Partition facts by the actual encoded ``put`` request size."""

    natural(maximum_frame_bytes, "maximum_frame_bytes", 1 << 30)
    natural(maximum_facts, "maximum_facts", MAX_PAGE_FACTS)
    if maximum_frame_bytes == 0 or maximum_facts == 0:
        raise ValueError("put limits must be positive")
    canonical = [canonical_pair(value)[0] for value in values]
    batches: list[list[str]] = []
    current: list[str] = []
    for text in canonical:
        candidate = current + [text]
        if (
            len(candidate) > maximum_facts
            or encoded_json_bytes({"op": "put", "facts": candidate}) > maximum_frame_bytes
        ):
            if not current:
                raise ValueError("one legal fact cannot fit in a put frame")
            batches.append(current)
            current = [text]
            if encoded_json_bytes({"op": "put", "facts": current}) > maximum_frame_bytes:
                raise ValueError("one legal fact cannot fit in a put frame")
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


@dataclass
class PageCursor:
    offset: int = 0
    completed_sweeps: int = 0

    def accept(self, response: object, limit: int,
               durable_accept: Callable[[list[str]], int]) -> tuple[int, int]:
        """Validate, durably insert the entire page, then advance the cursor.

        Malformed replies and failed durable insertion leave the cursor intact.
        A lost response is retried with the same request offset. Newly inserted
        facts can reorder an inventory; repeated full sweeps, not one pass, give
        completeness once the finite retained state stabilizes.
        """
        natural(self.offset, "offset", MAX_CURSOR)
        natural(limit, "limit", MAX_PAGE_FACTS)
        if limit == 0 or type(response) is not dict or set(response) != {
            "offset", "next_offset", "eof", "facts"
        }:
            raise ValueError("malformed fact page")
        if type(response["offset"]) is not int or response["offset"] != self.offset:
            raise ValueError("page does not match the requested offset")
        next_offset = natural(response["next_offset"], "next_offset", MAX_CURSOR)
        eof = response["eof"]
        raw = response["facts"]
        if type(eof) is not bool or type(raw) is not list or len(raw) > limit:
            raise ValueError("malformed page payload")
        if any(type(text) is not str for text in raw):
            raise ValueError("page facts must be canonical JSON strings")
        canonical = [canonical_pair(text)[0] for text in raw]
        if canonical != raw or canonical != sorted(set(canonical)):
            raise ValueError("page facts must be canonical, unique and ordered")
        if eof:
            if next_offset != 0:
                raise ValueError("completed sweep must wrap to zero")
        elif not raw or next_offset != self.offset + len(raw):
            raise ValueError("page must advance by its actual number of facts")
        added = durable_accept(canonical)
        self.offset = next_offset
        self.completed_sweeps += int(eof)
        return len(canonical), added
