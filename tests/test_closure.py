from __future__ import annotations

import asyncio
import ast
import inspect
from pathlib import Path
import tempfile
import unittest

import carat.independent_check as independent_check
from carat.closure import sealed_query
from carat.compaction import CompactedLedger, compact_sealed_batch
from carat.decode import decode
from carat.facts import MAX_SEAL_SPAN, FactError, canonical_text, mint, seal
from carat.independent_check import check_sealed
from carat.service import Cluster, NODE_NAMES, rpc


MASSES = {"n0": 2, "n1": 3, "n2": 4, "n3": 5, "n4": 1}
BATCH = "closed-window"


def closed_window() -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for origin in NODE_NAMES:
        facts.append(mint(f"{origin}:1", f"unit-{origin}", MASSES[origin], origin, BATCH))
        facts.append(seal(f"{origin}:2", f"seal-{origin}", BATCH, origin, 0, 1))
    return facts


async def exchange_all(cluster: Cluster, rounds: int = 3) -> None:
    for _round in range(rounds):
        for node in NODE_NAMES:
            for peer in NODE_NAMES:
                if node != peer:
                    await rpc(cluster.ports[node], {"op": "pull", "peer": peer, "limit": 32})


class ClosureTests(unittest.TestCase):
    def test_shape_requires_origin_owned_frontier_coordinate(self) -> None:
        self.assertEqual(
            seal("n0:2", "s0", "b", "n0", 0, 1)["frontier"], 1
        )
        with self.assertRaises(FactError):
            seal("n1:2", "s0", "b", "n0", 0, 1)
        with self.assertRaises(FactError):
            seal("n0:3", "s0", "b", "n0", 0, 1)
        with self.assertRaises(FactError):
            seal("n0:1", "s0", "b", "n0", 2, 1)

    def test_seal_span_and_missing_diagnostics_are_bounded(self) -> None:
        with self.assertRaisesRegex(FactError, "span exceeds"):
            seal(
                f"n0:{MAX_SEAL_SPAN + 2}",
                "too-wide",
                "b",
                "n0",
                0,
                MAX_SEAL_SPAN + 1,
            )

        bounded = seal("n0:100001", "wide-but-bounded", "b", "n0", 0, 100000)
        result = sealed_query([bounded], "b", ("n0",))
        individual = [
            item for item in result.violations
            if item.startswith("missing-sealed-coordinate:n0:")
        ]
        summaries = [
            item for item in result.violations
            if item.startswith("missing-sealed-coordinate-count:n0:")
        ]
        self.assertEqual(len(individual), 64)
        self.assertEqual(summaries, ["missing-sealed-coordinate-count:n0:100000"])

    def test_same_observed_accounting_can_be_open_or_complete(self) -> None:
        facts = closed_window()
        delayed = next(item for item in facts if item["event"] == "n3:1")
        observed = [item for item in facts if item is not delayed]

        provisional = decode(observed)
        self.assertTrue(provisional.determined)
        self.assertEqual(provisional.gross_mass, 10)

        open_result = sealed_query(observed, BATCH, NODE_NAMES)
        open_check = check_sealed(observed, BATCH, NODE_NAMES)
        self.assertFalse(open_result.final)
        self.assertFalse(open_check.accepted)
        self.assertIn("missing-sealed-coordinate:n3:1", open_result.violations)
        self.assertEqual(open_result.gross_mass, 10)

        complete = sealed_query(observed + [delayed], BATCH, NODE_NAMES)
        complete_check = check_sealed(observed + [delayed], BATCH, NODE_NAMES)
        self.assertTrue(complete.final, complete.violations)
        self.assertTrue(complete_check.accepted, complete_check.violations)
        self.assertEqual(complete.gross_mass, 15)
        self.assertEqual(complete.as_dict()["final"], complete_check.as_dict()["accepted"])

    def test_final_window_is_unchanged_by_later_batch(self) -> None:
        facts = closed_window()
        before = sealed_query(facts, BATCH, NODE_NAMES)
        later = mint("n0:3", "later-unit", 99, "n0", "later-window")
        after = sealed_query(facts + [later], BATCH, NODE_NAMES)
        self.assertTrue(before.final and after.final)
        self.assertEqual(before.gross_mass, after.gross_mass)
        self.assertEqual(before.unique_units, after.unique_units)

    def test_later_window_requires_a_genesis_anchored_seal_chain(self) -> None:
        first = closed_window()
        second_data = [
            mint(f"{origin}:3", f"second-{origin}", 1, origin, "second-window")
            for origin in NODE_NAMES
        ]
        second_seals = [
            seal(f"{origin}:4", f"second-seal-{origin}", "second-window", origin, 2, 3)
            for origin in NODE_NAMES
        ]
        complete = sealed_query(first + second_data + second_seals, "second-window", NODE_NAMES)
        checked = check_sealed(first + second_data + second_seals, "second-window", NODE_NAMES)
        self.assertTrue(complete.final, complete.violations)
        self.assertTrue(checked.accepted, checked.violations)
        self.assertEqual(complete.gross_mass, 5)

        unanchored = sealed_query(second_data + second_seals, "second-window", NODE_NAMES)
        self.assertFalse(unanchored.final)
        self.assertTrue(any(item.startswith("missing-predecessor-seal:") for item in unanchored.violations))

    def test_same_batch_fact_beyond_frontier_refuses_finality(self) -> None:
        facts = closed_window()
        facts.append(mint("n0:3", "outside-unit", 7, "n0", BATCH))
        result = sealed_query(facts, BATCH, NODE_NAMES)
        checked = check_sealed(facts, BATCH, NODE_NAMES)
        self.assertFalse(result.final)
        self.assertFalse(checked.accepted)
        self.assertIn("fact-outside-sealed-interval:n0:3", result.violations)


    def test_sealed_projection_retains_only_chain_anchors(self) -> None:
        facts = closed_window()
        ledger = compact_sealed_batch(facts, BATCH, NODE_NAMES)
        self.assertIsInstance(ledger, CompactedLedger)
        self.assertEqual(ledger.accounting()["gross_mass"], 15)
        self.assertEqual(len(ledger.summaries), 1)
        self.assertEqual(len(ledger.live_facts), len(NODE_NAMES))
        self.assertTrue(all('"kind":"seal"' in text for text in ledger.live_facts))

        second_data = [
            mint(f"{origin}:3", f"second-{origin}", 1, origin, "second-window")
            for origin in NODE_NAMES
        ]
        second_seals = [
            seal(f"{origin}:4", f"second-seal-{origin}", "second-window", origin, 2, 3)
            for origin in NODE_NAMES
        ]
        next_window = sealed_query(
            list(ledger.live_facts) + second_data + second_seals,
            "second-window",
            NODE_NAMES,
        )
        self.assertTrue(next_window.final, next_window.violations)
        self.assertEqual(next_window.gross_mass, 5)

    def test_sealed_projection_preserves_other_live_batches(self) -> None:
        first = closed_window()
        second_data = [
            mint(f"{origin}:3", f"second-{origin}", 1, origin, "second-window")
            for origin in NODE_NAMES
        ]
        second_seals = [
            seal(f"{origin}:4", f"second-seal-{origin}", "second-window", origin, 2, 3)
            for origin in NODE_NAMES
        ]
        complete = first + second_data + second_seals
        before = decode(complete)
        ledger = compact_sealed_batch(complete, BATCH, NODE_NAMES)
        after = ledger.accounting()

        self.assertTrue(after["determined"])
        self.assertEqual(after["unique_units"], before.unique_units)
        self.assertEqual(after["gross_mass"], before.gross_mass)
        self.assertEqual(after["gross_mass"], 20)
        self.assertEqual(sum(mass for _unit, mass in ledger.summaries[0].units), 15)

        still_live = sealed_query(ledger.live_facts, "second-window", NODE_NAMES)
        self.assertTrue(still_live.final, still_live.violations)
        self.assertEqual(still_live.gross_mass, 5)

    def test_sealed_projection_rejects_open_window_and_raw_replay(self) -> None:
        facts = closed_window()
        delayed = next(item for item in facts if item["event"] == "n3:1")
        observed = [item for item in facts if item is not delayed]
        with self.assertRaises(ValueError):
            compact_sealed_batch(observed, BATCH, NODE_NAMES)

        ledger = compact_sealed_batch(facts, BATCH, NODE_NAMES)
        replay = next(item for item in facts if item["kind"] == "mint")
        tampered = CompactedLedger(
            ledger.summaries, ledger.live_facts + (canonical_text(replay),)
        )
        self.assertFalse(tampered.accounting()["determined"])


    def test_admissible_later_subsets_preserve_finality(self) -> None:
        facts = closed_window()
        for mask in range(1 << len(NODE_NAMES)):
            later = [
                mint(f"{origin}:3", f"later-{origin}", index + 1, origin, "later-window")
                for index, origin in enumerate(NODE_NAMES)
                if mask & (1 << index)
            ]
            result = sealed_query(facts + later, BATCH, NODE_NAMES)
            checked = check_sealed(facts + later, BATCH, NODE_NAMES)
            self.assertTrue(result.final, (mask, result.violations))
            self.assertTrue(checked.accepted, (mask, checked.violations))
            self.assertEqual(result.gross_mass, 15)

    def test_global_identifier_reuse_is_an_inadmissible_extension(self) -> None:
        facts = closed_window()
        reused = mint("n0:3", "unit-n0", 99, "n0", "later-window")
        result = sealed_query(facts + [reused], BATCH, NODE_NAMES)
        checked = check_sealed(facts + [reused], BATCH, NODE_NAMES)
        self.assertFalse(result.final)
        self.assertFalse(checked.accepted)
        self.assertIn("global-mint-reuse:unit-n0", result.violations)
        self.assertEqual(result.violations, checked.violations)

    def test_closure_failure_matrix_agrees_with_independent_checker(self) -> None:
        complete = closed_window()
        cases: dict[str, list[dict[str, object]]] = {}
        cases["missing-seal"] = [
            item for item in complete
            if not (item["kind"] == "seal" and item["origin"] == "n4")
        ]
        cases["foreign-batch-coordinate"] = [
            ({**item, "batch": "foreign"} if item["event"] == "n2:1" else item)
            for item in complete
        ]
        cases["out-of-range"] = complete + [
            mint("n1:3", "outside", 7, "n1", BATCH)
        ]
        cases["global-reuse"] = complete + [
            mint("n0:3", "unit-n0", 7, "n0", "later-window")
        ]
        cases["missing-predecessor"] = [
            mint(f"{origin}:3", f"next-{origin}", 1, origin, "next")
            for origin in NODE_NAMES
        ] + [
            seal(f"{origin}:4", f"next-seal-{origin}", "next", origin, 2, 3)
            for origin in NODE_NAMES
        ]
        for name, values in cases.items():
            with self.subTest(name=name):
                result = sealed_query(values, "next" if name == "missing-predecessor" else BATCH, NODE_NAMES)
                checked = check_sealed(values, "next" if name == "missing-predecessor" else BATCH, NODE_NAMES)
                self.assertFalse(result.final)
                self.assertFalse(checked.accepted)
                self.assertEqual(result.violations, checked.violations)

    def test_checker_does_not_import_closure_implementation(self) -> None:
        syntax = ast.parse(inspect.getsource(independent_check))
        modules = [
            node.module or ""
            for node in ast.walk(syntax)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertFalse(any(module.endswith("closure") for module in modules))

    def test_service_delays_finality_until_late_fact_then_preserves_it(self) -> None:
        async def scenario() -> None:
            facts = closed_window()
            delayed = next(item for item in facts if item["event"] == "n3:1")
            observed = [item for item in facts if item is not delayed]
            with tempfile.TemporaryDirectory() as raw:
                cluster = Cluster(Path(raw))
                await cluster.start_all()
                try:
                    for index, fact in enumerate(observed):
                        node = NODE_NAMES[index % len(NODE_NAMES)]
                        await rpc(cluster.ports[node], {"op": "put", "facts": [fact]})
                    await exchange_all(cluster)
                    open_reports = [
                        (await rpc(cluster.ports[node], {"op": "finalize", "batch": BATCH}))[0]
                        for node in NODE_NAMES
                    ]
                    self.assertTrue(all(not row["accounting"]["final"] for row in open_reports))
                    self.assertEqual({row["accounting"]["gross_mass"] for row in open_reports}, {10})

                    await rpc(cluster.ports["n4"], {"op": "put", "facts": [delayed]})
                    await exchange_all(cluster)
                    final_reports = [
                        (await rpc(cluster.ports[node], {"op": "finalize", "batch": BATCH}))[0]
                        for node in NODE_NAMES
                    ]
                    self.assertTrue(all(row["accounting"]["final"] for row in final_reports))
                    self.assertEqual({row["accounting"]["gross_mass"] for row in final_reports}, {15})

                    await cluster.restart("n2")
                    restarted, _wire = await rpc(
                        cluster.ports["n2"], {"op": "finalize", "batch": BATCH}
                    )
                    self.assertTrue(restarted["accounting"]["final"])
                    self.assertEqual(restarted["accounting"]["gross_mass"], 15)

                    later = mint("n0:3", "later-unit", 99, "n0", "later-window")
                    await rpc(cluster.ports["n1"], {"op": "put", "facts": [later]})
                    await exchange_all(cluster)
                    stable = [
                        (await rpc(cluster.ports[node], {"op": "finalize", "batch": BATCH}))[0]
                        for node in NODE_NAMES
                    ]
                    self.assertTrue(all(row["accounting"]["final"] for row in stable))
                    self.assertEqual({row["accounting"]["gross_mass"] for row in stable}, {15})
                finally:
                    await cluster.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=20.0))


if __name__ == "__main__":
    unittest.main()
