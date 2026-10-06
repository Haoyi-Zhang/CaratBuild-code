"""Finite regressions for legal empty windows and single-pass API inputs."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from carat.closure import sealed_query
from carat.compaction import BatchSummary, compact_sealed_batch
from carat.facts import mint, seal
from carat.independent_check import check_sealed
from carat.retention import certify, issue_receipt, validate_certificate
from carat.service import NODE_NAMES


def empty_window():
    return [seal(f"{origin}:1", f"empty-{origin}", "empty", origin, 0, 0)
            for origin in NODE_NAMES]


class ScientificRegressionTests(unittest.TestCase):
    def test_projection_accepts_a_single_pass_origin_roster(self):
        facts = empty_window()
        expected = compact_sealed_batch(facts, "empty", NODE_NAMES)
        actual = compact_sealed_batch(iter(facts), "empty", iter(NODE_NAMES))
        self.assertEqual(actual, expected)
        self.assertTrue(actual.accounting()["determined"])

    def test_empty_window_receipts_and_projection_preserve_zero(self):
        facts = empty_window()
        self.assertTrue(sealed_query(facts, "empty", NODE_NAMES).final)
        self.assertTrue(check_sealed(facts, "empty", NODE_NAMES).accepted)
        for fault_bound in (0, 2, 4):
            with self.subTest(fault_bound=fault_bound):
                receipts = [issue_receipt(facts, "empty", NODE_NAMES, holder, fault_bound)
                            for holder in NODE_NAMES[:fault_bound + 1]]
                certificate = certify(receipts)
                validate_certificate(certificate, NODE_NAMES, NODE_NAMES)
                self.assertEqual(len(certificate.seal_ids), len(NODE_NAMES))
        ledger = compact_sealed_batch(facts, "empty", NODE_NAMES)
        self.assertEqual(ledger.accounting()["gross_mass"], 0)
        self.assertEqual(ledger.accounting()["unique_units"], 0)
        self.assertEqual(BatchSummary.from_dict(ledger.summaries[0].as_dict()),
                         ledger.summaries[0])

    def test_missing_empty_origin_seal_cannot_issue_a_receipt(self):
        with self.assertRaisesRegex(ValueError, "not independently finalized"):
            issue_receipt(empty_window()[:-1], "empty", NODE_NAMES, "n0", 0)

    def test_nonempty_projection_anchors_cannot_issue_a_raw_receipt(self):
        facts = [mint("n0:1", "u", 3, "n0", "nonempty"),
                 seal("n0:2", "s0", "nonempty", "n0", 0, 1)] + [
            seal(f"{origin}:1", f"s-{origin}", "nonempty", origin, 0, 0)
            for origin in NODE_NAMES[1:]]
        ledger = compact_sealed_batch(facts, "nonempty", NODE_NAMES)
        with self.assertRaisesRegex(ValueError, "not independently finalized"):
            issue_receipt(ledger.live_facts, "nonempty", NODE_NAMES, "n0", 0)

    def test_two_world_campaign_rejects_each_incorrect_mass(self):
        from carat import extended_experiments as campaigns

        for complete_mass, delayed_mass in ((9, 9), (9, 10), (10, 9), (11, 11)):
            with self.subTest(masses=(complete_mass, delayed_mass)):
                results = [SimpleNamespace(final=True, gross_mass=complete_mass),
                           SimpleNamespace(final=False, gross_mass=delayed_mass)]
                with patch.object(campaigns, "sealed_query", side_effect=results):
                    with self.assertRaisesRegex(AssertionError, "two-world discriminator"):
                        asyncio.run(campaigns.sealed_windows(Path("unused")))

    def test_two_world_campaign_accepts_the_correct_mass_pair(self):
        from carat import extended_experiments as campaigns

        # Stop at the next pure enumeration query, before any filesystem or TCP work.
        results = [SimpleNamespace(final=True, gross_mass=10),
                   SimpleNamespace(final=False, gross_mass=10),
                   RuntimeError("enumeration reached")]
        with patch.object(campaigns, "sealed_query", side_effect=results):
            with self.assertRaisesRegex(RuntimeError, "enumeration reached"):
                asyncio.run(campaigns.sealed_windows(Path("unused")))


if __name__ == "__main__":
    unittest.main()
