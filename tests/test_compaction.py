from __future__ import annotations

import unittest

from carat.compaction import compact_closed_batches
from carat.decode import decode
from carat.facts import effect, mint, presentation, transform
from carat.generate import mixed_history, recoordinate


class CompactionTests(unittest.TestCase):
    def test_closed_batch_preserves_accounting(self) -> None:
        scenarios = [mixed_history(90 + index * 7, 4 + index, index) for index in range(4)]
        facts = list(recoordinate(fact for scenario in scenarios for fact in scenario.facts))
        before = decode(facts)
        ledger = compact_closed_batches(facts, [f"hist-{index}" for index in range(4)])
        after = ledger.accounting()
        self.assertTrue(before.determined)
        self.assertTrue(after["determined"])
        for key in (
            "unique_units",
            "gross_mass",
            "presentations",
            "effects",
            "passing_effects",
            "failing_effects",
        ):
            self.assertEqual(getattr(before, key), after[key])
        raw_size = sum(len(str(fact)) for fact in facts)
        self.assertLess(ledger.serialized_bytes(), raw_size)

        negative_cases = {
            "live-presentation": (
                [
                    mint("n0:1", "u0", 1, "n0", "closed"),
                    presentation("n0:2", "p0", [("u0", 1)], "main", 0, "closed"),
                    presentation("n1:1", "p1", [("u0", 1)], "main", 1, "live"),
                ],
                "presentation crosses",
            ),
            "closed-presentation": (
                [
                    mint("n0:1", "u0", 1, "n0", "live"),
                    presentation("n0:2", "p0", [("u0", 1)], "main", 0, "closed"),
                ],
                "presentation crosses",
            ),
            "live-effect": (
                [
                    mint("n0:1", "u0", 1, "n0", "closed"),
                    presentation("n0:2", "p0", [("u0", 1)], "main", 0, "closed"),
                    effect("n1:1", "e0", "p0", "u0", "pass", "dep", "live"),
                ],
                "effect crosses",
            ),
            "closed-effect": (
                [
                    mint("n0:1", "u0", 1, "n0", "live"),
                    presentation("n0:2", "p0", [("u0", 1)], "main", 0, "live"),
                    effect("n1:1", "e0", "p0", "u0", "pass", "dep", "closed"),
                ],
                "effect crosses",
            ),
            "live-transform": (
                [
                    mint("n0:1", "u0", 1, "n0", "closed"),
                    presentation("n0:2", "p0", [("u0", 1)], "main", 0, "closed"),
                    presentation("n0:3", "p1", [("u0", 1)], "branch", 1, "closed"),
                    transform("n1:1", "t0", "rebase", ["p0"], ["p1"], [], "live"),
                ],
                "transform crosses",
            ),
            "closed-transform": (
                [
                    mint("n0:1", "u0", 1, "n0", "live"),
                    presentation("n0:2", "p0", [("u0", 1)], "main", 0, "live"),
                    presentation("n0:3", "p1", [("u0", 1)], "branch", 1, "live"),
                    transform("n1:1", "t0", "rebase", ["p0"], ["p1"], [], "closed"),
                ],
                "transform crosses",
            ),
        }
        for name, (case_facts, message) in negative_cases.items():
            with self.subTest(name=name):
                self.assertTrue(decode(case_facts).determined)
                with self.assertRaisesRegex(ValueError, message):
                    compact_closed_batches(case_facts, ["closed"])


if __name__ == "__main__":
    unittest.main()
