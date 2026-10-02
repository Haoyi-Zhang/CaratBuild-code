"""One-process-per-endpoint accounting service with bounded projection support."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .closure import sealed_query
from .compaction import BatchSummary, CompactedLedger, compact_sealed_batch
from .facts import canonical_pair, parse_text
from .independent_check import check, check_sealed
from .paged import (
    encoded_json_bytes,
    make_bounded_page,
    natural,
    MAX_PAGE_FACTS,
)
from .retention import RetentionCertificate, RetentionReceipt, issue_receipt, validate_certificate
from .service import (
    FactStore,
    MAX_FRAME_BYTES,
    NODE_NAMES,
    encode,
    receive,
)

MAX_RECEIPT_BYTES = 32_768
MAX_RECEIPT_LOG_BYTES = 2_097_152
MAX_RECEIPTS = 2_048


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, value: object) -> None:
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".next")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"projections": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or set(value) != {"projections"} or type(value["projections"]) is not list:
        raise ValueError("malformed projection state")
    return value


class IndependentNode:
    """A single durable endpoint.

    The process identity and TCP port are supplied by the local experiment
    controller.  The model assumes crash-stop endpoints and an authenticated
    fixed membership map; messages are not cryptographically signed.
    """

    def __init__(self, name: str, directory: Path):
        if name not in NODE_NAMES:
            raise ValueError("unknown endpoint")
        self.name = name
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.projection_path = directory / "projections.json"
        self.receipt_path = directory / "retention.jsonl"
        self.raw_pins: dict[str, RetentionReceipt] = {}
        self.recovered_receipt_tail_bytes = 0
        self.receipt_log_bytes = 0
        self._load_receipts()
        self.projections: dict[str, dict[str, Any]] = {}
        for raw in _read_json(self.projection_path)["projections"]:
            record = self._parse_projection(raw)
            if record["batch"] in self.projections:
                raise ValueError("duplicate projected batch")
            validate_certificate(record["certificate"], NODE_NAMES, NODE_NAMES)
            if self.name in record["certificate"].holders:
                raise ValueError("projected endpoint is named as a raw holder")
            self.projections[record["batch"]] = record
        self.store = FactStore(directory, name)
        self._validate_projection_anchors()
        pending = self._validate_pending_projection_sources()
        # Projection state is persisted before raw deletion.  If a process dies
        # between those writes, restart first reconstructs the exact summary from
        # the still-present raw facts and only then finishes deletion.
        filtered = [
            text for text in self.store.replica.facts
            if not (
                parse_text(text)["kind"] != "seal"
                and parse_text(text).get("batch") in self.projections
            )
        ]
        if self.projections:
            candidate = CompactedLedger(
                tuple(
                    self.projections[batch]["summary"]
                    for batch in sorted(self.projections)
                ),
                tuple(sorted(filtered)),
            )
            if not candidate.boundary_valid():
                raise ValueError("projected summaries conflict with retained state")
        if len(filtered) != len(self.store.replica.facts):
            self.store.replace_all(filtered)
        self._validate_projection_anchors()
        self.recovered_projection_deletions = len(pending)
        self.server: asyncio.Server | None = None
        self.writers: set[asyncio.StreamWriter] = set()
        self.stats = {
            "rpc_requests": 0,
            "request_bytes": 0,
            "response_bytes": 0,
            "projection_operations": 0,
            "receipt_operations": 0,
            "fenced_replays": 0,
            "idempotent_projected_replays": 0,
        }

    def _load_receipts(self) -> None:
        """Recover complete synchronized receipts and truncate only a torn tail.

        A newline-terminated malformed record is never discarded.  That case
        fails closed because it may represent acknowledged state whose meaning
        cannot be reconstructed.  Only a final record interrupted before its
        newline is treated as an unacknowledged process-crash tail.
        """

        if not self.receipt_path.exists():
            with self.receipt_path.open("xb") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            _sync_directory(self.directory)
        if self.receipt_path.stat().st_size > MAX_RECEIPT_LOG_BYTES:
            raise ValueError("retention receipt log exceeds bound")

        valid_end = 0
        records = 0
        with self.receipt_path.open("rb") as handle:
            while True:
                raw = handle.readline(MAX_RECEIPT_BYTES + 2)
                if not raw:
                    break
                if len(raw) > MAX_RECEIPT_BYTES + 1:
                    raise ValueError("retention receipt exceeds bound")
                if not raw.endswith(b"\n"):
                    if handle.read(1):
                        raise ValueError("unbounded incomplete retention receipt")
                    self.recovered_receipt_tail_bytes = len(raw)
                    break
                if raw == b"\n":
                    raise ValueError("empty complete retention receipt")
                receipt = RetentionReceipt.from_dict(json.loads(raw[:-1].decode("utf-8")))
                if receipt.holder != self.name:
                    raise ValueError("retention receipt belongs to another endpoint")
                previous = self.raw_pins.get(receipt.batch)
                if previous is not None and previous != receipt:
                    raise ValueError("conflicting durable retention receipts")
                self.raw_pins[receipt.batch] = receipt
                records += 1
                if records > MAX_RECEIPTS:
                    raise ValueError("retention receipt count exceeds bound")
                valid_end += len(raw)

        if self.recovered_receipt_tail_bytes:
            with self.receipt_path.open("r+b") as handle:
                handle.truncate(valid_end)
                handle.flush()
                os.fsync(handle.fileno())
        self.receipt_log_bytes = valid_end

    @staticmethod
    def _parse_projection(value: object) -> dict[str, Any]:
        if type(value) is not dict or set(value) != {
            "batch", "summary", "certificate", "final"
        }:
            raise ValueError("malformed projected batch")
        batch = value["batch"]
        summary = BatchSummary.from_dict(value["summary"])
        certificate = RetentionCertificate.from_dict(value["certificate"])
        final = value["final"]
        if type(batch) is not str or batch != summary.batch or batch != certificate.batch:
            raise ValueError("projection identity mismatch")
        if (type(final) is not dict or set(final) != {"unique_units", "gross_mass"}
                or type(final["unique_units"]) is not int or final["unique_units"] < 0
                or type(final["gross_mass"]) is not int or final["gross_mass"] < 0):
            raise ValueError("malformed projected final result")
        if (
            final["unique_units"] != len(summary.units)
            or final["gross_mass"] != sum(mass for _unit, mass in summary.units)
        ):
            raise ValueError("projected final result disagrees with its summary")
        return {
            "batch": batch,
            "summary": summary,
            "certificate": certificate,
            "final": dict(final),
        }

    def _validate_projection_anchors(self) -> None:
        """Bind each persisted projection to its retained origin-seal anchors."""

        seals: dict[str, list[str]] = {}
        for text in self.store.replica.facts:
            fact = parse_text(text)
            if fact["kind"] == "seal":
                seals.setdefault(fact["batch"], []).append(text)
        for batch, record in self.projections.items():
            summary: BatchSummary = record["summary"]
            observed = tuple(sorted(seals.get(batch, [])))
            if observed != summary.seal_anchors:
                raise ValueError("projected batch seal anchors disagree with its certificate")
            expected_ids = tuple(sorted(parse_text(text)["seal"] for text in observed))
            if expected_ids != record["certificate"].seal_ids:
                raise ValueError("projected batch seal identifiers disagree with its certificate")

    def _validate_pending_projection_sources(self) -> tuple[str, ...]:
        """Rebuild a persisted summary before completing interrupted deletion.

        Atomic fact-log replacement means a process crash leaves either the old
        complete raw set or the new anchor-only set.  When raw data remain, they
        are the strongest available recovery oracle: independently re-run sealed
        closure and compaction, then require byte-independent structural equality
        with the persisted summary and batch-local final result.  After deletion,
        summary authenticity is outside this crash-stop model and remains an
        explicit non-claim.
        """

        pending: list[str] = []
        facts = tuple(self.store.replica.facts)
        for batch in sorted(self.projections):
            has_raw = any(
                fact["kind"] != "seal" and fact.get("batch") == batch
                for fact in (parse_text(text) for text in facts)
            )
            if not has_raw:
                continue
            record = self.projections[batch]
            ledger = compact_sealed_batch(facts, batch, NODE_NAMES)
            if len(ledger.summaries) != 1 or ledger.summaries[0] != record["summary"]:
                raise ValueError("pending projection summary disagrees with raw facts")
            closed = sealed_query(facts, batch, NODE_NAMES)
            expected_final = {
                "unique_units": closed.unique_units,
                "gross_mass": closed.gross_mass,
            }
            if not closed.final or expected_final != record["final"]:
                raise ValueError("pending projection final result disagrees with raw facts")
            pending.append(batch)
        return tuple(pending)

    def _save_projections(self) -> None:
        rows = []
        for batch in sorted(self.projections):
            record = self.projections[batch]
            rows.append({
                "batch": batch,
                "summary": record["summary"].as_dict(),
                "certificate": record["certificate"].as_dict(),
                "final": record["final"],
            })
        _write_json(self.projection_path, {"projections": rows})

    def _ledger(self) -> CompactedLedger:
        summaries = tuple(self.projections[batch]["summary"] for batch in sorted(self.projections))
        return CompactedLedger(summaries, tuple(sorted(self.store.replica.facts)))

    def _closed_identifiers(self) -> dict[str, set[str]]:
        result = {"mint": set(), "presentation": set(), "transform": set(), "effect": set()}
        for record in self.projections.values():
            summary: BatchSummary = record["summary"]
            result["mint"].update(unit for unit, _mass in summary.units)
            result["presentation"].update(summary.presentation_ids)
            result["transform"].update(summary.transform_ids)
            result["effect"].update(summary.effect_ids)
        return result

    def _projection_indexes(self) -> tuple[set[str], dict[str, str], dict[str, str]]:
        """Return exact replay, reserved-coordinate, and seal-ID indexes."""

        replay: set[str] = set()
        reserved_events: dict[str, str] = {}
        seal_ids: dict[str, str] = {}
        for record in self.projections.values():
            summary: BatchSummary = record["summary"]
            for text in summary.replay_facts + summary.seal_anchors:
                fact = parse_text(text)
                previous = reserved_events.get(fact["event"])
                if previous is not None and previous != text:
                    raise ValueError("projected summaries reuse an event coordinate")
                reserved_events[fact["event"]] = text
                if fact["kind"] == "seal":
                    existing = seal_ids.get(fact["seal"])
                    if existing is not None and existing != text:
                        raise ValueError("projected summaries reuse a seal identifier")
                    seal_ids[fact["seal"]] = text
                else:
                    replay.add(text)
        return replay, reserved_events, seal_ids

    def _validate_admission(self, values: Iterable[str | dict[str, Any]]) -> list[str]:
        candidates = [canonical_pair(value) for value in values]
        texts: list[str] = []
        closed = self._closed_identifiers()
        replay, reserved_events, projected_seal_ids = self._projection_indexes()
        live = self.store.replica.facts
        for text, fact in candidates:
            if text in live:
                continue
            if text in replay:
                self.stats["idempotent_projected_replays"] += 1
                continue

            expected_at_coordinate = reserved_events.get(fact["event"])
            if expected_at_coordinate is not None and expected_at_coordinate != text:
                self.stats["fenced_replays"] += 1
                raise ValueError("event coordinate from a projected batch is fenced")

            if fact.get("batch") in self.raw_pins:
                self.stats["fenced_replays"] += 1
                raise ValueError("a durably pinned raw batch is immutable")

            if fact["kind"] == "seal":
                if fact.get("batch") in self.projections:
                    self.stats["fenced_replays"] += 1
                    raise ValueError("additional seal for a projected batch is fenced")
                previous = projected_seal_ids.get(fact["seal"])
                if previous is not None and previous != text:
                    self.stats["fenced_replays"] += 1
                    raise ValueError("seal identifier from a projected batch is fenced")
                texts.append(text)
                continue

            if fact["kind"] != "seal":
                if fact.get("batch") in self.projections:
                    self.stats["fenced_replays"] += 1
                    raise ValueError("raw fact for a projected batch is fenced")
                field = {
                    "mint": "unit",
                    "presentation": "presentation",
                    "transform": "transform",
                    "effect": "effect",
                }[fact["kind"]]
                if fact[field] in closed[fact["kind"]]:
                    self.stats["fenced_replays"] += 1
                    raise ValueError("identifier from a projected batch is fenced")
            texts.append(text)
        return texts

    def _append_receipt(self, receipt: dict[str, object]) -> None:
        payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > MAX_RECEIPT_BYTES + 1:
            raise ValueError("retention receipt exceeds bound")
        if self.receipt_log_bytes + len(payload) > MAX_RECEIPT_LOG_BYTES:
            raise ValueError("retention receipt log exceeds bound")
        with self.receipt_path.open("ab") as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError("short retention-receipt write")
            handle.flush()
            os.fsync(handle.fileno())
        self.receipt_log_bytes += len(payload)

    async def start(self, port: int = 0) -> int:
        self.server = await asyncio.start_server(
            self.handle,
            "127.0.0.1",
            port,
            limit=MAX_FRAME_BYTES + 4,
        )
        return int(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
        for writer in list(self.writers):
            writer.transport.abort()
        if self.server is not None:
            await self.server.wait_closed()
        self.server = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.add(writer)
        try:
            request, size = await receive(reader)
            self.stats["rpc_requests"] += 1
            self.stats["request_bytes"] += size
            if type(request) is not dict or type(request.get("op")) is not str:
                raise ValueError("malformed RPC request")
            try:
                result = self.dispatch(request)
                frame = encode({"ok": True, "result": result})
            except (ValueError, OSError, UnicodeError, RecursionError) as error:
                frame = encode({"ok": False, "error": type(error).__name__ + ": " + str(error)[:180]})
            writer.write(frame)
            await writer.drain()
            self.stats["response_bytes"] += len(frame)
        except (ValueError, OSError, UnicodeError, RecursionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            with suppress(OSError, asyncio.CancelledError):
                await writer.wait_closed()

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request["op"]
        if op == "put":
            if set(request) != {"op", "facts"} or type(request["facts"]) is not list:
                raise ValueError("put requires a fact list")
            if len(request["facts"]) > MAX_PAGE_FACTS:
                raise ValueError("put exceeds the fact-count frame bound")
            return {"added": self.store.accept(self._validate_admission(request["facts"]))}
        if op == "page":
            if set(request) != {"op", "offset", "limit"}:
                raise ValueError("page requires offset and limit")
            return make_bounded_page(
                self.store.replica.facts,
                request["offset"],
                request["limit"],
                MAX_FRAME_BYTES,
            )
        if op == "query":
            if set(request) != {"op"}:
                raise ValueError("query has no arguments")
            if self.projections:
                accounting = self._ledger().accounting()
                checked = {"accepted": bool(accounting["determined"]), "projection_aware": True}
            else:
                decoded = __import__("carat.decode", fromlist=["decode"]).decode(self.store.replica.facts)
                verified = check(self.store.replica.facts)
                accounting = decoded.as_dict()
                checked = verified.as_dict()
            return {
                "accounting": accounting,
                "checker": checked,
                "live_facts": len(self.store.replica.facts),
                "projected_batches": sorted(self.projections),
                "stored_bytes": self.store.bytes_on_disk + (self.projection_path.stat().st_size if self.projection_path.exists() else 0),
            }
        if op == "finalize":
            if set(request) != {"op", "batch"} or type(request["batch"]) is not str:
                raise ValueError("finalize requires a batch")
            batch = request["batch"]
            if batch in self.projections:
                self._validate_projection_anchors()
                if not self._ledger().boundary_valid():
                    raise ValueError("projected state conflicts with retained evidence")
                record = self.projections[batch]
                return {
                    "accounting": {
                        "batch": batch,
                        "final": True,
                        "closure_complete": True,
                        **record["final"],
                        "projected": True,
                    },
                    "checker": {"accepted": True, "projection_aware": True},
                }
            closed = sealed_query(self.store.replica.facts, batch, NODE_NAMES)
            verified = check_sealed(self.store.replica.facts, batch, NODE_NAMES)
            if (
                closed.final != verified.accepted
                or closed.gross_mass != verified.gross_mass
                or closed.unique_units != verified.unique_units
            ):
                raise ValueError("sealed decoder and checker disagree")
            return {"accounting": closed.as_dict(), "checker": verified.as_dict()}
        if op == "receipt":
            if set(request) != {"op", "batch", "max_crashes"}:
                raise ValueError("receipt requires batch and fault bound")
            if request["batch"] in self.projections:
                raise ValueError("projected endpoint cannot issue a raw-retention receipt")
            existing = self.raw_pins.get(request["batch"])
            if existing is not None:
                if existing.max_crashes != request["max_crashes"]:
                    raise ValueError("batch is already pinned under a different fault bound")
                return existing.as_dict()
            receipt = issue_receipt(
                self.store.replica.facts,
                request["batch"],
                NODE_NAMES,
                self.name,
                request["max_crashes"],
            )
            self._append_receipt(receipt.as_dict())
            self.raw_pins[receipt.batch] = receipt
            self.stats["receipt_operations"] += 1
            return receipt.as_dict()
        if op == "project":
            if set(request) != {"op", "certificate"}:
                raise ValueError("project requires a retention certificate")
            certificate = RetentionCertificate.from_dict(request["certificate"])
            validate_certificate(certificate, NODE_NAMES, NODE_NAMES)
            if certificate.batch in self.projections:
                return {"projected": False, "already_projected": True}
            if certificate.batch in self.raw_pins or self.name in certificate.holders:
                raise ValueError("a durably pinned raw holder may not project")
            local = issue_receipt(
                self.store.replica.facts,
                certificate.batch,
                NODE_NAMES,
                self.name,
                certificate.max_crashes,
            )
            if local.seal_ids != certificate.seal_ids:
                raise ValueError("retention certificate names a different sealed batch")
            before = self.store.bytes_on_disk
            ledger = compact_sealed_batch(self.store.replica.facts, certificate.batch, NODE_NAMES)
            if len(ledger.summaries) != 1:
                raise ValueError("projection expected exactly one batch summary")
            closed = sealed_query(self.store.replica.facts, certificate.batch, NODE_NAMES)
            if not closed.final:
                raise ValueError("projection requires a finalized batch")
            record = {
                "batch": certificate.batch,
                "summary": ledger.summaries[0],
                "certificate": certificate,
                "final": {
                    "unique_units": closed.unique_units,
                    "gross_mass": closed.gross_mass,
                },
            }
            # Persist the summary and f+1 certificate before deleting raw facts.
            self.projections[certificate.batch] = record
            self._save_projections()
            self.store.replace_all(ledger.live_facts)
            self._validate_projection_anchors()
            if not self._ledger().boundary_valid():
                raise ValueError("projection result conflicts with retained evidence")
            after = self.store.bytes_on_disk + self.projection_path.stat().st_size
            self.stats["projection_operations"] += 1
            return {
                "projected": True,
                "bytes_before": before,
                "bytes_after": after,
                "retained_seals": len(ledger.live_facts),
                "holders": list(certificate.holders),
            }
        if op == "export":
            if set(request) != {"op", "batch", "offset", "limit"} or type(request["batch"]) is not str:
                raise ValueError("export requires batch, offset and limit")
            if request["batch"] in self.projections:
                raise ValueError("projected endpoint has no raw batch to export")
            if request["batch"] not in self.raw_pins:
                raise ValueError("bounded complete export requires a durable raw pin")
            facts = [
                text for text in sorted(self.store.replica.facts)
                if parse_text(text).get("batch") == request["batch"]
            ]
            offset = natural(request["offset"], "offset", 300_000)
            limit = natural(request["limit"], "limit", MAX_PAGE_FACTS)
            if limit == 0:
                raise ValueError("export limit must be positive")
            manifest = {
                "batch": request["batch"],
                "total_facts": len(facts),
                "total_canonical_bytes": sum(len(text.encode("utf-8")) for text in facts),
                "seal_ids": list(self.raw_pins[request["batch"]].seal_ids),
            }
            selected = facts[offset:offset + limit]
            result = {
                "manifest": manifest,
                "offset": offset,
                "next_offset": 0 if offset + len(selected) >= len(facts) else offset + len(selected),
                "eof": offset + len(selected) >= len(facts),
                "facts": selected,
            }
            while encoded_json_bytes({"ok": True, "result": result}) > MAX_FRAME_BYTES:
                if len(result["facts"]) <= 1:
                    raise ValueError("one export fact cannot fit in the configured frame")
                result["facts"].pop()
                result["eof"] = False
                result["next_offset"] = offset + len(result["facts"])
            return result
        if op == "status":
            if set(request) != {"op"}:
                raise ValueError("status has no arguments")
            return {
                "name": self.name,
                "live_facts": len(self.store.replica.facts),
                "projected_batches": sorted(self.projections),
                "raw_pinned_batches": sorted(self.raw_pins),
                "fact_log_bytes": self.store.bytes_on_disk,
                "projection_bytes": self.projection_path.stat().st_size if self.projection_path.exists() else 0,
                "receipt_log_bytes": self.receipt_log_bytes,
                "recovered_receipt_tail_bytes": self.recovered_receipt_tail_bytes,
                "recovered_projection_deletions": self.recovered_projection_deletions,
                "stats": dict(self.stats),
            }
        raise ValueError("unknown operation")


async def serve_one(name: str, directory: Path, ready_file: Path, port: int = 0) -> None:
    node = IndependentNode(name, directory)
    bound = await node.start(port)
    ready_file.write_text(json.dumps({"name": name, "port": bound}, sort_keys=True) + "\n", encoding="utf-8")
    try:
        await asyncio.Event().wait()
    finally:
        await node.close()
