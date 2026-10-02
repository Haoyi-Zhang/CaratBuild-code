from __future__ import annotations

from itertools import permutations, product
import unittest

from carat.decode import decode
from carat.facts import canonical_text
from carat.generate import amplification_history, operation_sequence_history
from carat.independent_check import check
from carat.protocol import Replica, states_equal


class ExhaustiveTests(unittest.TestCase):
    def test_small_delivery_schedules(self) -> None:
        scenario = amplification_history([1, 2], 1, "rebase", salt=3)
        texts = [canonical_text(fact) for fact in scenario.facts[:5]]
        checked = 0
        for order in permutations(texts):
            for duplicate_mask in product((0, 1), repeat=3):
                replicas = [Replica("x0"), Replica("x1"), Replica("x2")]
                for index, text in enumerate(order):
                    receiver = replicas[index % 3]
                    receiver.append_text(text)
                    if index < 3 and duplicate_mask[index]:
                        receiver.append_text(text)
                for left in replicas:
                    for right in replicas:
                        left.merge(right)
                self.assertTrue(states_equal(replicas))
                for replica in replicas:
                    decoded = decode(replica.facts)
                    checked_state = check(replica.facts)
                    self.assertTrue(decoded.determined, decoded.witnesses)
                    self.assertTrue(checked_state.accepted, checked_state.violations)
                    self.assertEqual(decoded.gross_mass, scenario.oracle_mass)
                    self.assertEqual(checked_state.gross_mass, scenario.oracle_mass)
                checked += 1
        self.assertEqual(checked, 5 * 4 * 3 * 2 * 8)

    def test_small_operation_sequences(self) -> None:
        sequence_index = 0
        for length in range(1, 4):
            for operations in product(("rebase", "cherry_pick", "revert"), repeat=length):
                sequence_index += 1
                scenario = operation_sequence_history(
                    operations, masses=(2, 3, 5), salt=1000 + sequence_index
                )
                result = decode(scenario.facts)
                checked = check(scenario.facts)
                self.assertTrue(result.determined, (operations, result.witnesses))
                self.assertTrue(checked.accepted, (operations, checked.violations))
                self.assertEqual(result.gross_mass, 10)
                self.assertEqual(checked.gross_mass, 10)


if __name__ == "__main__":
    unittest.main()
