"""Lossless representations compared on exactly the same canonical facts."""

from __future__ import annotations

import base64
import json
import statistics
import time
import zlib
from collections import defaultdict
from typing import Iterable

from .compaction import BatchSummary, CompactedLedger, compact_closed_batches
from .decode import decode
from .facts import canonical_pair, parse_text


def compare_storage(values: Iterable[str | dict], repetitions: int = 5) -> dict:
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("repetitions must be positive")
    texts = tuple(sorted({canonical_pair(value)[0] for value in values}))
    groups = defaultdict(list)
    for text in texts:
        groups[parse_text(text)["batch"]].append(text)
    raw = ("\n".join(texts) + ("\n" if texts else "")).encode()
    compressed = zlib.compress(raw, level=9)
    per_batch = json.dumps(
        {batch: base64.b85encode(zlib.compress("\n".join(rows).encode(), 9)).decode()
         for batch, rows in sorted(groups.items())},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    ledger = compact_closed_batches(texts, groups)
    projected = json.dumps(ledger.as_dict(), sort_keys=True, separators=(",", ":")).encode()

    def unpack_batches():
        payload = json.loads(per_batch)
        return tuple(sorted(text for blob in payload.values()
                            for text in zlib.decompress(base64.b85decode(blob)).decode().split("\n")))

    def unpack_projection():
        payload = json.loads(projected)
        restored = CompactedLedger(tuple(BatchSummary.from_dict(item) for item in payload["summaries"]),
                                   tuple(payload["live_facts"]))
        return tuple(sorted(set(restored.live_facts).union(
            text for summary in restored.summaries for text in summary.replay_facts)))

    decoders = {
        "raw_jsonl": lambda: tuple(raw.decode().splitlines()),
        "whole_zlib": lambda: tuple(zlib.decompress(compressed).decode().splitlines()),
        "batch_zlib_json": unpack_batches,
        "projection": unpack_projection,
    }
    fields = ("unique_units", "gross_mass", "presentations", "effects", "passing_effects", "failing_effects")
    expected = decode(texts)
    if not expected.determined:
        raise ValueError("comparison requires a valid complete relation state")
    measurements = {}
    sizes = {"raw_jsonl": len(raw), "whole_zlib": len(compressed),
             "batch_zlib_json": len(per_batch), "projection": len(projected)}
    for name, unpack in decoders.items():
        samples = []
        for _ in range(repetitions):
            start = time.perf_counter_ns()
            recovered = unpack()
            actual = decode(recovered)
            samples.append(time.perf_counter_ns() - start)
            if recovered != texts or not actual.determined or any(
                getattr(actual, field) != getattr(expected, field) for field in fields
            ):
                raise AssertionError("lossless representation changed facts or accounting")
        measurements[name] = {"bytes": sizes[name], "roundtrip_equal": True,
                              "decode_and_account_median_ns": int(statistics.median(samples))}
    return {"batches": len(groups), "facts": len(texts), "repetitions": repetitions,
            "compression_level": 9, "metric": "resident encoded payload; decode plus identical full accounting",
            "representations": measurements}
