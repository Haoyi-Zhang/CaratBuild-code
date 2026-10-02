"""Five bounded localhost accounting endpoints with durable fact logs.

The executable research configuration runs five logical endpoints in one event
loop. It tests actual loopback framing, retry and disk reconstruction, not five
independent machines, wide-area performance, authentication or power-loss safety.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import struct
from typing import Any, Iterable

from .closure import sealed_query
from .decode import decode
from .facts import canonical_pair
from .independent_check import check, check_sealed
from .paged import PageCursor, make_bounded_page, natural, MAX_PAGE_FACTS
from .protocol import Replica

NODE_NAMES = tuple(f"n{index}" for index in range(5))
MAX_FRAME_BYTES = 1_048_576
MAX_FACT_BYTES = 32_768
MAX_STORE_BYTES = 33_554_432
MAX_STORE_FACTS = 2048
RPC_TIMEOUT_SECONDS = 2.0


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate RPC object field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite RPC number")


def encode(value: object) -> bytes:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")
    if not 1 <= len(data) <= MAX_FRAME_BYTES:
        raise ValueError("RPC frame exceeds the configured bound")
    return struct.pack("!I", len(data)) + data


async def receive(reader: asyncio.StreamReader) -> tuple[Any, int]:
    length = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 1 <= length <= MAX_FRAME_BYTES:
        raise ValueError("invalid RPC frame length")
    raw = await reader.readexactly(length)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                       parse_constant=_reject_constant)
    return value, length + 4


async def rpc(port: int, request: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """One request and one response, exclusively to the IPv4 loopback address."""
    natural(port, "port", 65535)
    if port == 0:
        raise ValueError("unbound peer")

    async def exchange() -> tuple[dict[str, Any], int]:
        reader, writer = await asyncio.open_connection("127.0.0.1", port,
                                                        limit=MAX_FRAME_BYTES + 4)
        try:
            outgoing = encode(request)
            writer.write(outgoing)
            await writer.drain()
            value, incoming_bytes = await receive(reader)
            if type(value) is not dict or set(value) not in ({"ok", "result"}, {"ok", "error"}):
                raise ValueError("malformed RPC response")
            if value["ok"] is not True:
                raise ValueError(str(value.get("error", "request refused")))
            if type(value["result"]) is not dict:
                raise ValueError("RPC result must be an object")
            return value["result"], len(outgoing) + incoming_bytes
        finally:
            writer.close()
            with suppress(OSError, asyncio.CancelledError):
                await writer.wait_closed()

    return await asyncio.wait_for(exchange(), RPC_TIMEOUT_SECONDS)


class FactStore:
    """Append-before-ack store; complete malformed records fail closed.

    Recovery may discard only an incomplete final record. An acknowledged batch
    was fsynced before its response. Filesystem or power-failure semantics beyond
    this ordering are not claimed. Cursors are ephemeral and reset on restart.
    """

    def __init__(self, directory: Path, name: str):
        if name not in NODE_NAMES:
            raise ValueError("unknown logical node")
        self.directory = directory
        self.path = directory / "facts.jsonl"
        self.replica = Replica(name)
        self.cursors = {peer: PageCursor() for peer in NODE_NAMES if peer != name}
        self.poisoned = False
        self.recovered_tail_bytes = 0
        self.bytes_on_disk = 0
        directory.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("xb") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            self._sync_directory()
        if self.path.stat().st_size > MAX_STORE_BYTES:
            raise ValueError("stored fact log exceeds bound")
        valid_end = 0
        with self.path.open("rb") as handle:
            while True:
                raw = handle.readline(MAX_FACT_BYTES + 2)
                if not raw:
                    break
                if len(raw) > MAX_FACT_BYTES + 1:
                    raise ValueError("stored fact exceeds bound")
                if not raw.endswith(b"\n"):
                    # readline reached EOF within the bound. Never discard an
                    # interior malformed newline-terminated record.
                    if handle.read(1):
                        raise ValueError("unbounded incomplete log record")
                    self.recovered_tail_bytes = len(raw)
                    break
                text = raw[:-1].decode("utf-8")
                canonical, _fact = canonical_pair(text)
                if canonical != text:
                    raise ValueError("stored fact is not canonical")
                self.replica.append_text(text)
                if len(self.replica.facts) > MAX_STORE_FACTS:
                    raise ValueError("stored fact count exceeds bound")
                valid_end += len(raw)
        if self.recovered_tail_bytes:
            with self.path.open("r+b") as handle:
                handle.truncate(valid_end)
                handle.flush()
                os.fsync(handle.fileno())
        self.bytes_on_disk = valid_end

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def replace_all(self, values: Iterable[str | dict[str, Any]]) -> None:
        """Atomically replace the durable fact set with canonical complete records."""
        canonical: set[str] = set()
        for value in values:
            text, _fact = canonical_pair(value)
            if len(text.encode("utf-8")) > MAX_FACT_BYTES:
                raise ValueError("fact exceeds service admission bound")
            canonical.add(text)
        ordered = sorted(canonical)
        payload = b"".join(text.encode("utf-8") + b"\n" for text in ordered)
        if len(ordered) > MAX_STORE_FACTS or len(payload) > MAX_STORE_BYTES:
            raise ValueError("replacement store exceeds bound")
        temporary = self.directory / "facts.next"
        with temporary.open("wb") as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError("short replacement fact-log write")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        self._sync_directory()
        self.replica = Replica(self.replica.name)
        for text in ordered:
            self.replica.append_text(text)
        self.bytes_on_disk = len(payload)
        self.poisoned = False
        self.recovered_tail_bytes = 0

    def accept(self, values: Iterable[str | dict[str, Any]]) -> int:
        if self.poisoned:
            raise OSError("store requires recovery after a failed write")
        canonical: set[str] = set()
        for value in values:
            text, _fact = canonical_pair(value)
            size = len(text.encode("utf-8"))
            if size > MAX_FACT_BYTES:
                raise ValueError("fact exceeds service admission bound")
            canonical.add(text)
        fresh = sorted(canonical - self.replica.facts)
        payload = b"".join(text.encode("utf-8") + b"\n" for text in fresh)
        if (len(self.replica.facts) + len(fresh) > MAX_STORE_FACTS
                or self.bytes_on_disk + len(payload) > MAX_STORE_BYTES):
            raise ValueError("store admission bound exceeded")
        if not fresh:
            return 0
        try:
            with self.path.open("ab") as handle:
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError("short fact-log write")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self.poisoned = True
            raise
        for text in fresh:
            self.replica.append_text(text)
        self.bytes_on_disk += len(payload)
        return len(fresh)


class Cluster:
    def __init__(self, directory: Path):
        self.directory = directory
        self.stores = {name: FactStore(directory / name, name) for name in NODE_NAMES}
        self.servers: dict[str, asyncio.Server] = {}
        self.ports: dict[str, int] = {}
        self.writers: dict[str, set[asyncio.StreamWriter]] = {name: set() for name in NODE_NAMES}
        self.faults = {name: {"drop_requests": 0, "drop_responses": 0,
                             "partial_responses": 0, "delay_responses": 0}
                       for name in NODE_NAMES}
        self.stats = {"rpc_requests": 0, "request_bytes": 0, "response_bytes": 0,
                      "page_requests": 0, "injected_request_drops": 0,
                      "injected_response_drops": 0, "injected_partial_responses": 0,
                      "injected_delays": 0, "logical_restarts": 0,
                      "largest_frame_bytes": 0, "largest_page_facts": 0}

    async def start(self, name: str, port: int = 0) -> None:
        if name in self.servers:
            raise ValueError("node already listening")
        server = await asyncio.start_server(
            lambda reader, writer: self.handle(name, reader, writer),
            "127.0.0.1", port, limit=MAX_FRAME_BYTES + 4)
        self.servers[name] = server
        self.ports[name] = server.sockets[0].getsockname()[1]

    async def start_all(self) -> None:
        for name in NODE_NAMES:
            await self.start(name)

    async def stop(self, name: str) -> None:
        server = self.servers.pop(name, None)
        if server:
            server.close()
        # Terminate accepted connections before waiting for listener closure;
        # otherwise an in-flight pull can hold the lifecycle operation open.
        for writer in list(self.writers[name]):
            writer.transport.abort()
        if server:
            await server.wait_closed()

    async def close(self) -> None:
        for name in list(self.servers):
            await self.stop(name)

    async def restart(self, name: str) -> None:
        old_port = self.ports[name]
        await self.stop(name)
        # The old in-memory replica and all scan positions are discarded.
        self.stores[name] = FactStore(self.directory / name, name)
        await self.start(name, old_port)
        self.stats["logical_restarts"] += 1

    async def handle(self, name: str, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        self.writers[name].add(writer)
        try:
            request, size = await asyncio.wait_for(receive(reader), RPC_TIMEOUT_SECONDS)
            self.stats["rpc_requests"] += 1
            self.stats["request_bytes"] += size
            self.stats["largest_frame_bytes"] = max(self.stats["largest_frame_bytes"], size)
            if type(request) is not dict or type(request.get("op")) is not str:
                raise ValueError("malformed RPC request")
            is_page = request["op"] == "page"
            if is_page:
                self.stats["page_requests"] += 1
                if self.faults[name]["drop_requests"]:
                    self.faults[name]["drop_requests"] -= 1
                    self.stats["injected_request_drops"] += 1
                    writer.transport.abort()
                    return
            try:
                result = await self.dispatch(name, request)
                frame = encode({"ok": True, "result": result})
            except (ValueError, OSError, UnicodeError, RecursionError,
                    asyncio.IncompleteReadError, asyncio.TimeoutError) as error:
                frame = encode({"ok": False, "error": type(error).__name__ + ": " + str(error)[:180]})
            if is_page and self.faults[name]["drop_responses"]:
                self.faults[name]["drop_responses"] -= 1
                self.stats["injected_response_drops"] += 1
                writer.transport.abort()
                return
            if is_page and self.faults[name]["partial_responses"]:
                self.faults[name]["partial_responses"] -= 1
                self.stats["injected_partial_responses"] += 1
                fragment = frame[:max(4, len(frame) // 2)]
                writer.write(fragment)
                await writer.drain()
                self.stats["response_bytes"] += len(fragment)
                return
            if is_page and self.faults[name]["delay_responses"]:
                self.faults[name]["delay_responses"] -= 1
                self.stats["injected_delays"] += 1
                await asyncio.sleep(0.002)
            writer.write(frame)
            await writer.drain()
            self.stats["response_bytes"] += len(frame)
            self.stats["largest_frame_bytes"] = max(self.stats["largest_frame_bytes"], len(frame))
        except (ValueError, OSError, UnicodeError, RecursionError,
                asyncio.IncompleteReadError, asyncio.TimeoutError):
            # Malformed, oversized or interrupted frames receive no success.
            pass
        finally:
            self.writers[name].discard(writer)
            writer.close()
            with suppress(OSError, asyncio.CancelledError):
                await writer.wait_closed()

    async def dispatch(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
        op = request["op"]
        store = self.stores[name]
        if op == "put":
            if set(request) != {"op", "facts"} or type(request["facts"]) is not list:
                raise ValueError("put requires a fact list")
            if len(request["facts"]) > MAX_PAGE_FACTS:
                raise ValueError("put exceeds the fact-count frame bound")
            return {"added": store.accept(request["facts"])}
        if op == "page":
            if set(request) != {"op", "offset", "limit"}:
                raise ValueError("page requires offset and limit")
            page = make_bounded_page(
                store.replica.facts,
                request["offset"],
                request["limit"],
                MAX_FRAME_BYTES,
            )
            self.stats["largest_page_facts"] = max(self.stats["largest_page_facts"], len(page["facts"]))
            return page
        if op == "pull":
            if set(request) != {"op", "peer", "limit"}:
                raise ValueError("pull requires peer and limit")
            peer = request["peer"]
            if type(peer) is not str or peer not in store.cursors:
                raise ValueError("unknown peer")
            natural(request["limit"], "limit", MAX_PAGE_FACTS)
            cursor = store.cursors[peer]
            before = cursor.offset
            page, wire_bytes = await rpc(self.ports[peer], {
                "op": "page", "offset": before, "limit": request["limit"]})
            # A pull can await peer I/O while another endpoint restarts this
            # node. Never append to a detached in-memory store after recovery.
            if self.stores[name] is not store or name not in self.servers:
                raise ValueError("endpoint lifecycle changed during pull")
            received, added = cursor.accept(page, request["limit"], store.accept)
            return {"offset_before": before, "offset_after": cursor.offset,
                    "received": received, "added": added,
                    "completed_sweeps": cursor.completed_sweeps,
                    "data_rpc_bytes": wire_bytes}
        if op == "query":
            if set(request) != {"op"}:
                raise ValueError("query has no arguments")
            decoded = decode(store.replica.facts)
            checked = check(store.replica.facts)
            return {"accounting": decoded.as_dict(), "checker": checked.as_dict(),
                    "facts": len(store.replica.facts), "stored_bytes": store.bytes_on_disk,
                    "tail_recovered_bytes": store.recovered_tail_bytes,
                    "cursors": {peer: cursor.offset for peer, cursor in store.cursors.items()}}
        if op == "finalize":
            if set(request) != {"op", "batch"} or type(request["batch"]) is not str:
                raise ValueError("finalize requires a batch")
            closed = sealed_query(store.replica.facts, request["batch"], NODE_NAMES)
            verified = check_sealed(store.replica.facts, request["batch"], NODE_NAMES)
            if (
                closed.final != verified.accepted
                or closed.closure_complete != verified.closure_complete
                or closed.gross_mass != verified.gross_mass
                or closed.unique_units != verified.unique_units
            ):
                raise ValueError("sealed decoder and checker disagree")
            return {
                "accounting": closed.as_dict(),
                "checker": verified.as_dict(),
                "facts": len(store.replica.facts),
                "stored_bytes": store.bytes_on_disk,
            }
        if op == "control":
            if set(request) != {"op", "action", "node", "value"}:
                raise ValueError("control requires action, node and value")
            target = request["node"]
            action = request["action"]
            if type(target) is not str or target not in NODE_NAMES or type(action) is not str:
                raise ValueError("unknown control target")
            if action in self.faults[target]:
                self.faults[target][action] = natural(request["value"], "count", 16)
            elif action in {"stop", "restart"}:
                if target == name or type(request["value"]) is not int or request["value"] != 0:
                    raise ValueError("node lifecycle control requires another endpoint")
                if action == "stop":
                    await self.stop(target)
                else:
                    await self.restart(target)
            else:
                raise ValueError("unknown bounded control action")
            return {"applied": True}
        if op == "stats":
            if set(request) != {"op"}:
                raise ValueError("stats has no arguments")
            return dict(self.stats)
        raise ValueError("unknown operation")


async def serve(directory: Path, ready_file: Path) -> None:
    cluster = Cluster(directory)
    await cluster.start_all()
    ready_file.write_text(json.dumps(cluster.ports, sort_keys=True) + "\n", encoding="utf-8")
    try:
        await asyncio.Event().wait()
    finally:
        await cluster.close()
