from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from carat.baselines import unique_mint_mass
from carat.boundaries import transport_pair
from carat.compaction import BatchSummary, CompactedLedger, compact_closed_batches
from carat.decode import decode
from carat.facts import canonical_text, mint, presentation, transform, effect
from carat.generate import mixed_history, ambiguity_variant
from carat.paged import PageCursor, make_page
from carat.protocol import Replica
from carat.service import Cluster, FactStore, rpc


def accept_into(replica: Replica, values: list[str]) -> int:
    return sum(int(replica.append_text(text)) for text in values)


class BoundaryTests(unittest.TestCase):
    def test_identity_baseline_separates_mass_from_refusal(self) -> None:
        scenario = mixed_history(31, 3, 901)
        invalid = ambiguity_variant(scenario, "invalid-transform", 1)
        self.assertEqual(unique_mint_mass(scenario.facts), scenario.oracle_mass)
        self.assertEqual(unique_mint_mass(invalid), scenario.oracle_mass)
        self.assertTrue(decode(scenario.facts).determined)
        self.assertFalse(decode(invalid).determined)

    def test_pages_transfer_permanent_holes_and_coordinate_conflicts(self) -> None:
        for name in ("prefix-complete", "permanent-holes", "split-coordinate"):
            left, right = transport_pair(name)
            expected = left.facts | right.facts
            cursors = (PageCursor(), PageCursor())
            for _ in range(20):
                for sender, receiver, cursor in ((left, right, cursors[0]), (right, left, cursors[1])):
                    page = make_page(sender.facts, cursor.offset, 16)
                    cursor.accept(page, 16, lambda values, r=receiver: accept_into(r, values))
            self.assertEqual(left.facts, expected)
            self.assertEqual(right.facts, expected)
            self.assertEqual(decode(expected).determined, name != "split-coordinate")

    def test_cursor_survives_malformed_response_and_failed_write(self) -> None:
        fact = canonical_text(mint("n0:1", "u", 1, "n0", "window"))
        cursor = PageCursor()
        good = make_page([fact], 0, 1)
        bad_cases = [dict(good, offset=True), dict(good, next_offset=True),
                     dict(good, eof=1), dict(good, facts=[fact, fact]),
                     dict(good, eof=False, next_offset=0),
                     dict(good, facts=[" " + fact]), dict(good, extra=1)]
        for bad in bad_cases:
            with self.assertRaises(ValueError):
                cursor.accept(bad, 1, lambda values: len(values))
            self.assertEqual(cursor.offset, 0)
            self.assertEqual(cursor.completed_sweeps, 0)
        def failure(_values: list[str]) -> int:
            raise OSError("bounded injected persistence failure")
        with self.assertRaises(OSError):
            cursor.accept(good, 1, failure)
        self.assertEqual(cursor.completed_sweeps, 0)
        cursor.accept(good, 1, lambda values: len(values))
        self.assertEqual(cursor.completed_sweeps, 1)

    def test_repeated_sweeps_cover_insertions_before_a_scan_position(self) -> None:
        source = Replica("n0")
        receiver = Replica("n1")
        for unit, seq in (("b", 2), ("c", 3)):
            source.append(mint(f"n0:{seq}", unit, 1, "n0", "window"))
        cursor = PageCursor()
        cursor.accept(make_page(source.facts, 0, 1), 1,
                      lambda values: accept_into(receiver, values))
        source.append(mint("n0:1", "a", 1, "n0", "window"))
        for _ in range(8):
            cursor.accept(make_page(source.facts, cursor.offset, 1), 1,
                          lambda values: accept_into(receiver, values))
        self.assertEqual(source.facts, receiver.facts)

    def test_durable_reconstruction_and_incomplete_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = FactStore(directory, "n0")
            fact = mint("n0:1", "u", 5, "n0", "window")
            self.assertEqual(store.accept([fact]), 1)
            self.assertEqual(store.accept([fact]), 0)
            store.cursors["n1"].offset = 7
            with store.path.open("ab") as handle:
                handle.write(b'{"pa')
            recovered = FactStore(directory, "n0")
            self.assertEqual(recovered.replica.facts, store.replica.facts)
            self.assertEqual(recovered.recovered_tail_bytes, 4)
            self.assertEqual(recovered.cursors["n1"].offset, 0)
            self.assertEqual(recovered.path.stat().st_size, recovered.bytes_on_disk)

    def test_malformed_complete_record_is_not_silently_erased(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = FactStore(directory, "n0")
            with store.path.open("ab") as handle:
                handle.write(b'{"bad":true}\n')
            before = store.path.read_bytes()
            with self.assertRaises(ValueError):
                FactStore(directory, "n0")
            self.assertEqual(store.path.read_bytes(), before)

    def test_batch_validation_precedes_disk_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = FactStore(Path(temporary), "n0")
            fact = mint("n0:1", "u", 5, "n0", "window")
            malformed = dict(fact, mass=True)
            with self.assertRaises(ValueError):
                store.accept([fact, malformed])
            self.assertEqual(store.replica.facts, set())
            self.assertEqual(store.path.read_bytes(), b"")

    def test_persistence_failure_poison_requires_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = FactStore(Path(temporary), "n0")
            fact = mint("n0:1", "u", 5, "n0", "window")
            with patch("carat.service.os.fsync", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    store.accept([fact])
            self.assertEqual(store.replica.facts, set())
            with self.assertRaises(OSError):
                store.accept([fact])
            recovered = FactStore(Path(temporary), "n0")
            # An unacknowledged complete write may survive. This is permitted;
            # the guarantee is preservation of acknowledged facts, not rollback.
            self.assertEqual(recovered.replica.facts, {canonical_text(fact)})

    def test_compacted_projection_refuses_stale_replay_and_overlap(self) -> None:
        closed_facts = [
            mint("n0:1", "u", 5, "n0", "closed"),
            presentation("n0:2", "p", [("u", 1)], "main", 0, "closed"),
            presentation("n0:3", "q", [("u", 1)], "topic", 1, "closed"),
            transform("n0:4", "t", "rebase", ["p"], ["q"], [], "closed"),
            effect("n0:5", "e", "q", "u", "pass", "fixture", "closed")]
        base = compact_closed_batches(closed_facts, ["closed"])
        self.assertTrue(base.accounting()["determined"])
        for fact in closed_facts:
            replay = replace(base, live_facts=(canonical_text(fact),))
            self.assertFalse(replay.accounting()["determined"])
        duplicate = replace(base, summaries=base.summaries + base.summaries)
        self.assertFalse(duplicate.accounting()["determined"])
        alias = BatchSummary("other", (("u", 5),), (), (), (), ())
        self.assertFalse(replace(base, summaries=base.summaries + (alias,)).accounting()["determined"])
        live = mint("n1:1", "u", 5, "n1", "live")
        self.assertFalse(replace(base, live_facts=(canonical_text(live),)).accounting()["determined"])


class ServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_endpoints_use_tcp_and_durable_page_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cluster = Cluster(Path(temporary))
            await cluster.start_all()
            try:
                fact = mint("n0:1", "u", 5, "n0", "window")
                added, _ = await rpc(cluster.ports["n0"], {"op": "put", "facts": [fact]})
                self.assertEqual(added["added"], 1)
                for name in ("n1", "n2", "n3", "n4"):
                    result, _ = await rpc(cluster.ports[name], {"op": "pull", "peer": "n0", "limit": 16})
                    self.assertEqual(result["added"], 1)
                await rpc(cluster.ports["n0"], {"op": "control", "action": "restart", "node": "n1", "value": 0})
                answer, _ = await rpc(cluster.ports["n1"], {"op": "query"})
                self.assertTrue(answer["accounting"]["determined"])
                self.assertEqual(answer["accounting"]["gross_mass"], 5)
            finally:
                await cluster.close()

    async def test_partial_tcp_reply_does_not_advance_receiver_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cluster = Cluster(Path(temporary))
            await cluster.start_all()
            try:
                fact = mint("n0:1", "u", 5, "n0", "window")
                await rpc(cluster.ports["n0"], {"op": "put", "facts": [fact]})
                await rpc(cluster.ports["n1"], {"op": "control", "action": "partial_responses", "node": "n0", "value": 1})
                with self.assertRaises(ValueError):
                    await rpc(cluster.ports["n1"], {"op": "pull", "peer": "n0", "limit": 16})
                answer, _ = await rpc(cluster.ports["n1"], {"op": "query"})
                self.assertEqual(answer["cursors"]["n0"], 0)
                self.assertEqual(answer["facts"], 0)
                result, _ = await rpc(cluster.ports["n1"], {"op": "pull", "peer": "n0", "limit": 16})
                self.assertEqual(result["added"], 1)
            finally:
                await cluster.close()


if __name__ == "__main__":
    unittest.main()


class LifecycleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_pull_cannot_write_into_replaced_store(self) -> None:
        import carat.service as service
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            cluster = Cluster(Path(temporary))
            await cluster.start_all()
            started, release = asyncio.Event(), asyncio.Event()
            original_rpc = service.rpc
            pull = None
            old_store = cluster.stores["n1"]
            async def delayed_rpc(port, request):
                if request["op"] == "page" and port == cluster.ports["n0"]:
                    started.set()
                    await release.wait()
                return await original_rpc(port, request)
            try:
                await rpc(cluster.ports["n0"], {"op": "put", "facts": [
                    mint("n0:1", "lifecycle-unit", 1, "n0", "lifecycle")]})
                with patch.object(service, "rpc", delayed_rpc):
                    pull = asyncio.create_task(rpc(cluster.ports["n1"],
                        {"op": "pull", "peer": "n0", "limit": 16}))
                    await asyncio.wait_for(started.wait(), 2)
                    await rpc(cluster.ports["n2"], {"op": "control", "node": "n1",
                        "action": "restart", "value": 0})
                    release.set()
                    with self.assertRaises((OSError, ValueError, asyncio.IncompleteReadError)):
                        await pull
                    # Wait for the detached handler to observe the epoch change.
                    for _ in range(10):
                        await asyncio.sleep(0.001)
                    self.assertFalse(old_store.replica.facts)
                    self.assertFalse(cluster.stores["n1"].replica.facts)
                reply, _ = await rpc(cluster.ports["n1"],
                    {"op": "pull", "peer": "n0", "limit": 16})
                self.assertEqual(reply["added"], 1)
            finally:
                release.set()
                if pull is not None:
                    if not pull.done(): pull.cancel()
                    await asyncio.gather(pull, return_exceptions=True)
                await cluster.close()
