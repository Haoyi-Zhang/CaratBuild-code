import unittest

from carat.generate import mixed_history, recoordinate
from carat.storage_comparison import compare_storage


class StorageComparisonTests(unittest.TestCase):
    def test_all_representations_recover_identical_facts(self):
        facts = recoordinate(fact for i in range(4) for fact in mixed_history(20, 3, i).facts)
        result = compare_storage(facts, repetitions=1)
        self.assertEqual(result["batches"], 4)
        self.assertTrue(all(row["roundtrip_equal"] for row in result["representations"].values()))
        self.assertLess(result["representations"]["whole_zlib"]["bytes"],
                        result["representations"]["projection"]["bytes"])

    def test_repetitions_reject_invalid_values(self):
        for value in (0, -1, True):
            with self.assertRaises(ValueError):
                compare_storage([], value)
