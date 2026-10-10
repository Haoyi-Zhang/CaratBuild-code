"""Small in-process raw-export regressions; no listener, disk or worker launch."""
from types import SimpleNamespace
import unittest

from carat.closure import sealed_query
from carat.facts import canonical_text, mint, parse_text, seal
from carat.independent_check import check_sealed
from carat.multiprocess_service import IndependentNode, _raw_export_facts
from carat.retention import issue_receipt
from carat.service import NODE_NAMES


def fixture(batches=3):
    facts = set()
    for index in range(batches):
        batch = f"batch-{index}"
        previous = 2 * index
        for origin in NODE_NAMES:
            facts.add(canonical_text(mint(
                f"{origin}:{previous + 1}", f"{batch}-{origin}",
                index + 1, origin, batch,
            )))
            facts.add(canonical_text(seal(
                f"{origin}:{previous + 2}", f"seal-{batch}-{origin}",
                batch, origin, previous, previous + 1,
            )))
    return facts


class ExportContextTests(unittest.TestCase):
    def test_genesis_package_has_no_support_context(self):
        raw = fixture()
        values, data_count, support_count = _raw_export_facts(raw, "batch-0")
        self.assertEqual((len(values), data_count, support_count), (10, 5, 0))
        self.assertTrue(check_sealed(values, "batch-0", NODE_NAMES).accepted)

    def test_later_batch_pages_include_transitive_seals_not_earlier_data(self):
        raw = fixture()
        batch = "batch-2"
        # Bypass only construction/persistence: dispatch itself is the real
        # implementation. This test neither starts a TCP server nor writes logs.
        node = object.__new__(IndependentNode)
        node.store = SimpleNamespace(replica=SimpleNamespace(facts=raw))
        node.projections = {}
        node.raw_pins = {batch: issue_receipt(raw, batch, NODE_NAMES, "n0", 0)}
        values = []
        manifest = None
        offset = 0
        for _ in range(10):
            page = node.dispatch({
                "op": "export", "batch": batch, "offset": offset, "limit": 3,
            })
            self.assertEqual(page["offset"], offset)
            if manifest is None:
                manifest = page["manifest"]
            self.assertEqual(page["manifest"], manifest)
            values.extend(page["facts"])
            if page["eof"]:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        else:
            self.fail("toy export did not finish within ten pages")
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(manifest["total_facts"], len(values))
        self.assertEqual(manifest["total_canonical_bytes"],
                         sum(len(text.encode("utf-8")) for text in values))
        self.assertEqual((manifest["target_data_facts"], manifest["target_seals"],
                          manifest["predecessor_seals"]), (5, 5, 10))
        self.assertEqual(manifest["seal_ids"], list(node.raw_pins[batch].seal_ids))
        self.assertTrue(all(parse_text(text)["kind"] == "seal"
                            or parse_text(text)["batch"] == batch for text in values))
        decoded = sealed_query(values, batch, NODE_NAMES)
        checked = check_sealed(values, batch, NODE_NAMES)
        self.assertTrue(decoded.final)
        self.assertTrue(checked.accepted)
        self.assertEqual((decoded.unique_units, decoded.gross_mass), (5, 15))
        self.assertEqual((checked.unique_units, checked.gross_mass), (5, 15))

    def test_missing_toy_predecessor_is_not_silently_omitted(self):
        raw = fixture()
        raw = {text for text in raw if parse_text(text)["event"] != "n0:2"}
        with self.assertRaisesRegex(ValueError, "predecessor seal"):
            _raw_export_facts(raw, "batch-2")


if __name__ == "__main__":
    unittest.main()
