from __future__ import annotations

import itertools
import json
from pathlib import Path
import tempfile
import unittest

from carat.closure import sealed_query
from carat.compaction import BatchSummary, compact_sealed_batch
from carat.facts import canonical_text, effect, mint, parse_text, presentation, seal, transform
from carat.multiprocess_service import IndependentNode
from carat.paged import PageCursor, encoded_json_bytes, make_page, split_put_batches
from carat.retention import RetentionCertificate, certify, issue_receipt, surviving_holders, validate_certificate
from carat.service import NODE_NAMES, MAX_FACT_BYTES, MAX_FRAME_BYTES, encode


def fixture():
    batch = "closed"
    facts = [
        mint("n0:1", "u", 5, "n0", batch),
        presentation("n0:2", "p0", [("u", 1)], "main", 0, batch),
        presentation("n0:3", "p1", [("u", 1)], "main", 1, batch),
        transform("n0:4", "t", "rebase", ["p0"], ["p1"], [], batch),
        effect("n0:5", "e", "p1", "u", "pass", "task", batch),
        seal("n0:6", "s0", batch, "n0", 0, 5),
    ]
    for name in NODE_NAMES[1:]:
        facts.append(seal(f"{name}:1", f"s-{name}", batch, name, 0, 0))
    return batch, facts


def two_window_fixture():
    first, facts = fixture()
    second = "later"
    for name in NODE_NAMES:
        if name == "n0":
            facts.append(mint("n0:7", "later-n0", 1, "n0", second))
            facts.append(seal("n0:8", "later-seal-n0", second, "n0", 6, 7))
        else:
            facts.append(mint(f"{name}:2", f"later-{name}", 1, name, second))
            facts.append(seal(f"{name}:3", f"later-seal-{name}", second, name, 1, 2))
    return first, second, facts


class RetentionTests(unittest.TestCase):
    def test_f_plus_one_threshold_is_tight(self):
        batch, facts = fixture()
        receipts = [
            issue_receipt(facts, batch, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ]
        certificate = certify(receipts)
        with self.assertRaises(ValueError):
            certify(receipts[:2])
        for count in range(3):
            for failed in itertools.combinations(NODE_NAMES, count):
                self.assertTrue(surviving_holders(certificate, failed))
        self.assertEqual(surviving_holders(certificate, ("n0", "n1")), ("n2",))
        with self.assertRaises(ValueError):
            surviving_holders(certificate, ("n0", "n1", "n2"))

    def test_fixed_membership_and_durable_holder_pin(self):
        batch, facts = fixture()
        receipts = [
            issue_receipt(facts, batch, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ]
        certificate = certify(receipts)
        validate_certificate(certificate, NODE_NAMES, NODE_NAMES)
        outsider = RetentionCertificate(
            batch, 2, certificate.seal_ids, ("n1", "n2", "outside")
        )
        with self.assertRaises(ValueError):
            validate_certificate(outsider, NODE_NAMES, NODE_NAMES)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n0"
            node = IndependentNode("n0", directory)
            node.store.accept(facts)
            recorded = node.dispatch({"op": "receipt", "batch": batch, "max_crashes": 2})
            self.assertEqual(recorded["holder"], "n0")
            recovered = IndependentNode("n0", directory)
            alternate = RetentionCertificate(
                batch, 2, certificate.seal_ids, ("n1", "n2", "n3")
            )
            with self.assertRaises(ValueError):
                recovered.dispatch({"op": "project", "certificate": alternate.as_dict()})
            self.assertEqual(recovered.dispatch({"op": "status"})["raw_pinned_batches"], [batch])

    def test_projection_summary_fences_every_identifier_class(self):
        batch, facts = fixture()
        ledger = compact_sealed_batch(facts, batch, NODE_NAMES)
        summary = ledger.summaries[0]
        self.assertEqual(summary.presentation_ids, ("p0", "p1"))
        self.assertEqual(summary.transform_ids, ("t",))
        self.assertEqual(summary.effect_ids, ("e",))

        future = [
            mint("n1:2", "u", 5, "n1", "future"),
            presentation("n1:2", "p0", [("fresh", 1)], "main", 0, "future"),
            transform("n1:2", "t", "rebase", ["x"], ["y"], [], "future"),
            effect("n1:2", "e", "x", "fresh", "pass", "task", "future"),
        ]
        for fact in future:
            candidate = type(ledger)(ledger.summaries, (ledger.live_facts + ()))
            # Add one replay/reuse at a time. The projection boundary must fail
            # before the aggregate can be treated as authoritative.
            candidate = type(ledger)(candidate.summaries, candidate.live_facts + (
                __import__("carat.facts", fromlist=["canonical_text"]).canonical_text(fact),
            ))
            self.assertFalse(candidate.accounting()["determined"])

    def test_projected_endpoint_recovers_from_its_own_files(self):
        batch, facts = fixture()
        receipts = [
            issue_receipt(facts, batch, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ]
        certificate = certify(receipts)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n3"
            node = IndependentNode("n3", directory)
            node.store.accept(facts)
            result = node.dispatch({"op": "project", "certificate": certificate.as_dict()})
            self.assertTrue(result["projected"])
            recovered = IndependentNode("n3", directory)
            final = recovered.dispatch({"op": "finalize", "batch": batch})
            self.assertTrue(final["accounting"]["final"])
            self.assertTrue(final["accounting"]["projected"])
            self.assertEqual(final["accounting"]["gross_mass"], 5)
            replay = recovered.dispatch({"op": "put", "facts": [facts[0]]})
            self.assertEqual(replay["added"], 0)
            self.assertEqual(
                recovered.dispatch({"op": "status"})["stats"]["idempotent_projected_replays"],
                1,
            )

    def test_projected_adjudication_fences_visible_conflicts_and_restart(self):
        batch, facts = fixture()
        certificate = certify([
            issue_receipt(facts, batch, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ])
        conflicts = {
            "same-batch-extra-seal": seal(
                "n1:2", "extra-same-batch-seal", batch, "n1", 0, 1
            ),
            "cross-batch-reused-seal-id": seal(
                "n1:2", "s0", "future", "n1", 1, 1
            ),
            "deleted-coordinate-new-unit": mint(
                "n0:1", "new-unit-at-deleted-coordinate", 1, "n0", "future"
            ),
        }
        for label, conflict in conflicts.items():
            with self.subTest(label=label):
                # A raw reference replica can see the conflicting fact and must
                # revoke the old batch's finality.
                raw = sealed_query(facts + [conflict], batch, NODE_NAMES)
                self.assertFalse(raw.final, raw.as_dict())

                # The normal projected-node admission path rejects the same
                # visible conflict transactionally and preserves its old result.
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / "n3"
                    node = IndependentNode("n3", directory)
                    node.store.accept(facts)
                    node.dispatch({"op": "project", "certificate": certificate.as_dict()})
                    before = node.dispatch({"op": "query"})["accounting"]
                    self.assertTrue(node.dispatch({"op": "finalize", "batch": batch})["accounting"]["final"])
                    with self.assertRaisesRegex(ValueError, "fenced"):
                        node.dispatch({"op": "put", "facts": [conflict]})
                    self.assertEqual(node.dispatch({"op": "query"})["accounting"], before)
                    recovered = IndependentNode("n3", directory)
                    self.assertTrue(
                        recovered.dispatch({"op": "finalize", "batch": batch})["accounting"]["final"]
                    )

                # Simulate a legacy/bypassed admission that persisted the
                # conflict. Query and finalization must fail closed, and restart
                # must not resurrect a cached True result.
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / "n3"
                    node = IndependentNode("n3", directory)
                    node.store.accept(facts)
                    node.dispatch({"op": "project", "certificate": certificate.as_dict()})
                    node.store.accept([conflict])
                    self.assertFalse(node.dispatch({"op": "query"})["accounting"]["determined"])
                    with self.assertRaisesRegex(
                        ValueError,
                        "conflicts with retained evidence|seal anchors disagree",
                    ):
                        node.dispatch({"op": "finalize", "batch": batch})
                    with self.assertRaises(ValueError):
                        IndependentNode("n3", directory)

    def test_projected_transfer_skips_exact_replay_and_accepts_later_batch(self):
        first, second, all_facts = two_window_fixture()
        first_facts = [fact for fact in all_facts if fact["batch"] == first]
        certificate = certify([
            issue_receipt(first_facts, first, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n3"
            node = IndependentNode("n3", directory)
            node.store.accept(first_facts)
            node.dispatch({"op": "project", "certificate": certificate.as_dict()})

            inventory = [canonical_text(fact) for fact in all_facts]
            cursor = PageCursor()
            while cursor.completed_sweeps == 0:
                page = make_page(inventory, cursor.offset, 32)
                cursor.accept(
                    page,
                    32,
                    lambda values: node.dispatch({"op": "put", "facts": values})["added"],
                )

            first_final = node.dispatch({"op": "finalize", "batch": first})
            second_final = node.dispatch({"op": "finalize", "batch": second})
            self.assertTrue(first_final["accounting"]["final"])
            self.assertTrue(second_final["accounting"]["final"], second_final)
            self.assertGreater(
                node.dispatch({"op": "status"})["stats"]["idempotent_projected_replays"],
                0,
            )
            self.assertEqual(node.dispatch({"op": "query"})["accounting"]["gross_mass"], 10)

            recovered = IndependentNode("n3", directory)
            self.assertTrue(recovered.dispatch({"op": "finalize", "batch": first})["accounting"]["final"])
            self.assertTrue(recovered.dispatch({"op": "finalize", "batch": second})["accounting"]["final"])
            self.assertEqual(recovered.dispatch({"op": "query"})["accounting"]["gross_mass"], 10)

    def test_wire_pages_and_export_are_byte_bounded_above_one_megabyte(self):
        batch = "large-wire-batch"
        facts = [
            mint(
                f"n0:{index + 1}",
                f"large-unit-{index}-" + "x" * 32660,
                1,
                "n0",
                batch,
            )
            for index in range(32)
        ]
        facts.append(seal("n0:33", "large-seal-n0", batch, "n0", 0, 32))
        for name in NODE_NAMES[1:]:
            facts.append(seal(f"{name}:1", f"large-seal-{name}", batch, name, 0, 0))
        texts = [canonical_text(fact) for fact in facts]
        data_texts = texts[:32]
        self.assertLessEqual(max(len(text.encode("utf-8")) for text in texts), MAX_FACT_BYTES)
        self.assertGreater(
            encoded_json_bytes({"op": "put", "facts": data_texts}),
            MAX_FRAME_BYTES,
        )

        batches = split_put_batches(texts, MAX_FRAME_BYTES, 32)
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(encode({"op": "put", "facts": part})) <= MAX_FRAME_BYTES + 4 for part in batches))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n0"
            node = IndependentNode("n0", directory)
            for part in batches:
                node.dispatch({"op": "put", "facts": part})
            self.assertTrue(node.dispatch({"op": "finalize", "batch": batch})["accounting"]["final"])

            offset = 0
            paged: list[str] = []
            for _ in range(8):
                page = node.dispatch({"op": "page", "offset": offset, "limit": 32})
                self.assertLessEqual(len(encode({"ok": True, "result": page})), MAX_FRAME_BYTES + 4)
                paged.extend(page["facts"])
                if page["eof"]:
                    break
                offset = page["next_offset"]
            self.assertEqual(set(paged), set(texts))

            node.dispatch({"op": "receipt", "batch": batch, "max_crashes": 0})
            offset = 0
            exported: list[str] = []
            manifest = None
            for _ in range(8):
                page = node.dispatch({
                    "op": "export", "batch": batch, "offset": offset, "limit": 32,
                })
                self.assertLessEqual(len(encode({"ok": True, "result": page})), MAX_FRAME_BYTES + 4)
                if manifest is None:
                    manifest = page["manifest"]
                else:
                    self.assertEqual(page["manifest"], manifest)
                exported.extend(page["facts"])
                if page["eof"]:
                    break
                offset = page["next_offset"]
            self.assertIsNotNone(manifest)
            self.assertEqual(manifest["total_facts"], len(texts))
            self.assertEqual(
                manifest["total_canonical_bytes"],
                sum(len(text.encode("utf-8")) for text in texts),
            )
            self.assertEqual(set(exported), set(texts))

    def test_receipt_log_recovers_only_an_incomplete_final_record(self):
        batch, facts = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n0"
            node = IndependentNode("n0", directory)
            node.store.accept(facts)
            node.dispatch({"op": "receipt", "batch": batch, "max_crashes": 2})
            receipt_path = directory / "retention.jsonl"
            valid_size = receipt_path.stat().st_size
            with receipt_path.open("ab") as handle:
                handle.write(b'{"batch":"interrupted"')
                handle.flush()
            recovered = IndependentNode("n0", directory)
            status = recovered.dispatch({"op": "status"})
            self.assertEqual(status["raw_pinned_batches"], [batch])
            self.assertGreater(status["recovered_receipt_tail_bytes"], 0)
            self.assertEqual(status["receipt_log_bytes"], valid_size)
            self.assertEqual(receipt_path.stat().st_size, valid_size)

    def test_receipt_log_rejects_a_malformed_complete_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n0"
            IndependentNode("n0", directory)
            with (directory / "retention.jsonl").open("ab") as handle:
                handle.write(b"{}\n")
                handle.flush()
            with self.assertRaisesRegex(ValueError, "malformed retention receipt"):
                IndependentNode("n0", directory)

    def test_projection_recovery_completes_pending_raw_deletion(self):
        batch, facts = fixture()
        receipts = [
            issue_receipt(facts, batch, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ]
        certificate = certify(receipts)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n3"
            node = IndependentNode("n3", directory)
            node.store.accept(facts)
            ledger = compact_sealed_batch(facts, batch, NODE_NAMES)
            summary = ledger.summaries[0]
            projection = {
                "projections": [{
                    "batch": batch,
                    "summary": summary.as_dict(),
                    "certificate": certificate.as_dict(),
                    "final": {
                        "unique_units": len(summary.units),
                        "gross_mass": sum(mass for _unit, mass in summary.units),
                    },
                }]
            }
            (directory / "projections.json").write_text(
                json.dumps(projection, sort_keys=True) + "\n", encoding="utf-8"
            )
            recovered = IndependentNode("n3", directory)
            status = recovered.dispatch({"op": "status"})
            self.assertEqual(status["live_facts"], len(NODE_NAMES))
            self.assertEqual(status["recovered_projection_deletions"], 1)
            final = recovered.dispatch({"op": "finalize", "batch": batch})
            self.assertTrue(final["accounting"]["projected"])
            self.assertEqual(final["accounting"]["gross_mass"], 5)

    def test_projection_recovery_refuses_tampered_pending_summary(self):
        batch, facts = fixture()
        receipts = [
            issue_receipt(facts, batch, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ]
        certificate = certify(receipts)
        ledger = compact_sealed_batch(facts, batch, NODE_NAMES)
        summary = ledger.summaries[0]
        base = {
            "batch": batch,
            "summary": summary.as_dict(),
            "certificate": certificate.as_dict(),
            "final": {
                "unique_units": len(summary.units),
                "gross_mass": sum(mass for _unit, mass in summary.units),
            },
        }

        def unit_mass(record):
            record["summary"]["units"][0][1] = 6
            record["final"]["gross_mass"] = 6

        def presentation_id(record):
            record["summary"]["presentation_ids"][0] = "tampered-presentation"

        def transform_id(record):
            record["summary"]["transform_ids"][0] = "tampered-transform"

        def effect_aggregate(record):
            record["summary"]["effects"][0][1] = "fail"

        def effect_id(record):
            record["summary"]["effect_ids"][0] = "tampered-effect"

        cases = {
            "unit-mass": unit_mass,
            "presentation-id": presentation_id,
            "transform-id": transform_id,
            "effect-aggregate": effect_aggregate,
            "effect-id": effect_id,
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "n3"
                node = IndependentNode("n3", directory)
                node.store.accept(facts)
                record = json.loads(json.dumps(base))
                mutate(record)
                (directory / "projections.json").write_text(
                    json.dumps({"projections": [record]}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "projection summary disagrees with exact replay index|pending projection summary disagrees with raw facts",
                ):
                    IndependentNode("n3", directory)

    def test_projection_reports_batch_local_result_with_later_live_window(self):
        first, second, facts = two_window_fixture()
        receipts = [
            issue_receipt(facts, first, NODE_NAMES, holder, 2)
            for holder in NODE_NAMES[:3]
        ]
        certificate = certify(receipts)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n3"
            node = IndependentNode("n3", directory)
            node.store.accept(facts)
            result = node.dispatch({"op": "project", "certificate": certificate.as_dict()})
            self.assertTrue(result["projected"])

            projected = node.dispatch({"op": "finalize", "batch": first})
            self.assertEqual(projected["accounting"]["unique_units"], 1)
            self.assertEqual(projected["accounting"]["gross_mass"], 5)

            later = node.dispatch({"op": "finalize", "batch": second})
            self.assertTrue(later["accounting"]["final"], later)
            self.assertEqual(later["accounting"]["gross_mass"], 5)
            combined = node.dispatch({"op": "query"})
            self.assertEqual(combined["accounting"]["unique_units"], 6)
            self.assertEqual(combined["accounting"]["gross_mass"], 10)

    def test_projection_state_refuses_cross_summary_identifier_overlap(self):
        first, second, facts = two_window_fixture()
        summaries = {
            batch: compact_sealed_batch(facts, batch, NODE_NAMES).summaries[0]
            for batch in (first, second)
        }
        certificates = {
            batch: certify([
                issue_receipt(facts, batch, NODE_NAMES, holder, 2)
                for holder in NODE_NAMES[:3]
            ])
            for batch in (first, second)
        }
        second_fact = mint("n4:100", summaries[first].units[0][0], 1, "n4", second)
        second_anchors = tuple(
            canonical_text(seal(
                f"{name}:{102 if name == 'n4' else 2}",
                f"independent-{name}", second, name,
                100 if name == "n4" else 1,
                101 if name == "n4" else 1,
            ))
            for name in NODE_NAMES
        )
        independent_second = BatchSummary(
            batch=second,
            units=((summaries[first].units[0][0], 1),),
            presentation_ids=(),
            transform_ids=(),
            effects=(),
            effect_ids=(),
            replay_facts=(canonical_text(second_fact),),
            seal_anchors=tuple(sorted(second_anchors)),
        )
        tampered_second = independent_second.as_dict()
        BatchSummary.from_dict(tampered_second)
        certificates[second] = RetentionCertificate(
            second,
            2,
            tuple(sorted(parse_text(text)["seal"] for text in independent_second.seal_anchors)),
            tuple(NODE_NAMES[:3]),
        )

        records = []
        for batch, summary in (
            (first, summaries[first].as_dict()),
            (second, tampered_second),
        ):
            parsed = BatchSummary.from_dict(summary)
            records.append({
                "batch": batch,
                "summary": summary,
                "certificate": certificates[batch].as_dict(),
                "final": {
                    "unique_units": len(parsed.units),
                    "gross_mass": sum(mass for _unit, mass in parsed.units),
                },
            })

        anchors = list(summaries[first].seal_anchors) + list(independent_second.seal_anchors)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n3"
            node = IndependentNode("n3", directory)
            node.store.accept(anchors)
            (directory / "projections.json").write_text(
                json.dumps({"projections": records}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError, "projected summaries conflict with retained state"
            ):
                IndependentNode("n3", directory)

    def test_projection_state_fails_closed_on_malformed_complete_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "n3"
            node = IndependentNode("n3", directory)
            del node
            (directory / "projections.json").write_text(
                '{"projections":[{}]}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "malformed projected batch"):
                IndependentNode("n3", directory)


if __name__ == "__main__":
    unittest.main()
