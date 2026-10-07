"""Finite accounting reference and public-API regressions; no files or services.

The reference scans definitions and individual atoms. It covers mint,
presentation, effect and seal facts, not transformations or wire parsing.
Transformation predicates are checked separately by the unchanged checker.
"""
from __future__ import annotations

from copy import deepcopy
import importlib
from itertools import product
import unittest
from unittest.mock import patch

from carat.closure import sealed_query
from carat.compaction import BatchSummary, compact_sealed_batch
from carat.decode import decode
from carat.facts import canonical_pair, canonical_text, effect, mint, presentation, seal
from carat.generate import amplification_history, ambiguity_variant, mixed_history
from carat.independent_check import check, check_sealed, verify_witness


def scan_accounting_reference(values):
    """Literal finite definition/atom scans, independent of decoder indexes."""
    texts = sorted({canonical_pair(value)[0] for value in values})
    rows = [canonical_pair(text) for text in texts]
    if any(fact["kind"] == "transform" for _, fact in rows):
        raise ValueError("scan reference excludes transformation facts")
    witnesses = []

    def emit(category, code, facts, missing, detail):
        witnesses.append({
            "category": category, "code": code, "facts": sorted(facts),
            "missing": sorted(missing), "detail": detail,
        })

    for event in sorted({fact["event"] for _, fact in rows}):
        matches = [text for text, fact in rows if fact["event"] == event]
        if len(matches) > 1:
            emit("conflicting-equivalence", "event-equivocation", matches[:2], [],
                 f"event coordinate {event} carries two different facts")

    contracts = (
        ("mint", "unit", "unit-minted-more-than-once",
         "unit {id} has more than one mint fact"),
        ("presentation", "presentation", "presentation-equivocation",
         "presentation {id} has incompatible contents"),
        ("effect", "effect", "effect-equivocation",
         "effect {id} has conflicting observations"),
    )
    unique = {}
    for kind, field, code, message in contracts:
        ids = sorted({fact[field] for _, fact in rows if fact["kind"] == kind})
        unique[kind] = []
        for identifier in ids:
            matches = [(text, fact) for text, fact in rows
                       if fact["kind"] == kind and fact[field] == identifier]
            if len(matches) != 1:
                emit("conflicting-equivalence", code,
                     [text for text, _ in matches[:2]], [], message.format(id=identifier))
            else:
                unique[kind].append(matches[0])

    def has_mint(unit):
        return any(fact["kind"] == "mint" and fact["unit"] == unit for _, fact in rows)

    for text, fact in unique["presentation"]:
        for unit, _ in fact["atoms"]:
            if not has_mint(unit):
                emit("missing-event", "presentation-missing-mint", [text], [f"mint:{unit}"],
                     f"presentation {fact['presentation']} refers to unobserved unit {unit}")
    for text, fact in unique["effect"]:
        identifier = fact["effect"]
        matches = [(ptext, p) for ptext, p in unique["presentation"]
                   if p["presentation"] == fact["presentation"]]
        if not matches:
            emit("unresolved-effect", "effect-missing-presentation", [text],
                 [f"presentation:{fact['presentation']}"],
                 f"effect {identifier} refers to an unobserved presentation")
            continue
        ptext, pres = matches[0]
        if not any(unit == fact["unit"] for unit, _ in pres["atoms"]):
            emit("unresolved-effect", "effect-unit-not-in-presentation", [text, ptext], [],
                 f"effect {identifier} does not attach to a unit in its presentation")
        if not has_mint(fact["unit"]):
            emit("unresolved-effect", "effect-missing-mint", [text], [f"mint:{fact['unit']}"],
                 f"effect {identifier} refers to an unobserved unit")

    def key(witness):
        return (witness["category"], witness["code"],
                tuple(witness["facts"]), tuple(witness["missing"]))

    distinct = {key(witness): witness for witness in witnesses}
    return {
        "determined": not distinct,
        "unique_units": len(unique["mint"]),
        "gross_mass": sum(fact["mass"] for _, fact in unique["mint"]),
        "presentations": len(unique["presentation"]),
        "effects": len(unique["effect"]),
        "passing_effects": sum(fact["outcome"] == "pass" for _, fact in unique["effect"]),
        "failing_effects": sum(fact["outcome"] == "fail" for _, fact in unique["effect"]),
        "witnesses": [distinct[k] for k in sorted(distinct)],
    }


def wide_fixture(width=4, count=8, outcome="pass", sign=1, presentations=1):
    batch = "finite-effects"
    facts = [mint(f"n0:{i + 1}", f"u{i}", i + 1, "n0", batch) for i in range(width)]
    for index in range(presentations):
        facts.append(presentation(
            f"n0:{width + index + 1}", f"p{index}",
            [(f"u{i}", sign) for i in range(width)], "main", index, batch))
    for index in range(count):
        facts.append(effect(
            f"n0:{width + presentations + index + 1}", f"e{index:03}",
            f"p{index % presentations}", f"u{index % width}", outcome, "opaque", batch))
    return facts


def subset_fixtures():
    pool = wide_fixture(2, 2)
    pool += [
        presentation("n1:1", "p0", [("u0", -1)], "other", 0, "finite-effects"),
        effect("n1:2", "bad", "absent", "missing", "skip", "opaque", "finite-effects"),
        effect("n0:1", "e000", "p0", "u1", "fail", "opaque", "finite-effects"),
    ]
    for mask in range(1 << len(pool)):
        yield f"subset-{mask:03}", [fact for i, fact in enumerate(pool) if mask & (1 << i)]


class EffectMembershipTests(unittest.TestCase):
    def assert_scan(self, facts):
        snapshot = deepcopy(facts)
        actual = decode(iter(facts)).as_dict()
        self.assertEqual(actual, scan_accounting_reference(facts))
        self.assertEqual(facts, snapshot)
        report = check(facts)
        self.assertEqual(report.accepted, actual["determined"])
        self.assertEqual(report.gross_mass, actual["gross_mass"])
        return actual

    def test_named_wide_fixtures(self):
        self.assert_scan([])
        for args in product((1, 4, 16), (0, 1, 8, 32),
                            ("pass", "fail", "skip"), (-1, 1), (1, 2)):
            with self.subTest(width_count_outcome_sign_presentations=args):
                self.assert_scan(wide_fixture(*args))

    def test_complete_finite_subsets(self):
        for name, facts in subset_fixtures():
            with self.subTest(name=name):
                self.assert_scan(facts)

    def test_order_duplicate_and_call_lifetime(self):
        facts = wide_fixture()
        expected = self.assert_scan(facts)
        texts = [canonical_text(fact) for fact in facts]
        for values in (list(reversed(facts)), facts + facts, list(reversed(texts)) + texts):
            self.assertEqual(decode(iter(values)).as_dict(), expected)
        facts[4]["atoms"] = [["u0", -1]]
        changed = self.assert_scan(facts)
        self.assertFalse(changed["determined"])
        changed["witnesses"].clear()
        self.assert_scan(facts)
        facts[4]["atoms"] = [[f"u{i}", 1] for i in range(4)]
        self.assertEqual(self.assert_scan(facts), expected)

    def test_independent_witness_support(self):
        for name, facts in subset_fixtures():
            for witness in decode(facts).as_dict()["witnesses"]:
                with self.subTest(name=name, code=witness["code"]):
                    self.assertTrue(verify_witness(facts, witness)[0])
                    for field in ("facts", "missing"):
                        for index in range(len(witness[field])):
                            damaged = deepcopy(witness)
                            damaged[field].pop(index)
                            self.assertFalse(verify_witness(facts, damaged)[0])
                    unrelated = mint("n9:1", "unrelated", 1, "n9", "other")
                    oversized = deepcopy(witness)
                    oversized["facts"].append(canonical_text(unrelated))
                    self.assertFalse(verify_witness(facts + [unrelated], oversized)[0])
        module = importlib.import_module("carat.decode")
        with patch.object(module, "decode", side_effect=AssertionError("producer used by checker")):
            self.assertTrue(check(wide_fixture()).accepted)

    def test_transform_and_mutation_controls(self):
        for operation, copies in product(("rebase", "cherry_pick", "fork", "squash", "revert"), (1, 2)):
            scenario = amplification_history([2, 3, 5], copies, operation, salt=31)
            result = decode(scenario.facts)
            self.assertTrue(result.determined)
            self.assertTrue(check(scenario.facts).accepted)
            self.assertEqual(result.gross_mass, 10)
            self.assertEqual(result.unique_units, 3)
            self.assertEqual(result.effects, sum(f["kind"] == "effect" for f in scenario.facts))
        scenario = mixed_history(13, 3, 7, rewrite_rounds=2)
        for index, variant in enumerate(("missing-transform-output", "conflicting-presentation",
                                        "unresolved-effect", "conflicting-event", "invalid-transform")):
            facts = ambiguity_variant(scenario, variant, index)
            result = decode(facts)
            self.assertFalse(result.determined)
            self.assertFalse(check(facts).accepted)
            self.assertTrue(result.witnesses)
            for witness in result.witnesses:
                self.assertTrue(verify_witness(facts, witness.as_dict())[0])

    def test_local_atom_walks_and_projection(self):
        module = importlib.import_module("carat.decode")
        original_parse = module.parse_text
        walks = {}

        class CountedAtoms(list):
            def __init__(self, values, identifier):
                super().__init__(values)
                self.identifier = identifier

            def __iter__(self):
                walks[self.identifier] = walks.get(self.identifier, 0) + 1
                return super().__iter__()

        def counted_parse(text):
            fact = original_parse(text)
            if fact["kind"] == "presentation":
                fact["atoms"] = CountedAtoms(fact["atoms"], fact["presentation"])
            return fact

        for count in (0, 1, 8, 32):
            walks.clear()
            facts = wide_fixture(16, count, presentations=2)
            with patch.object(module, "parse_text", side_effect=counted_parse):
                result = decode(facts).as_dict()
            self.assertEqual(result, scan_accounting_reference(facts))
            self.assertEqual(walks, {"p0": 1 + int(count > 0), "p1": 1 + int(count > 1)})

        facts = wide_fixture(4, 8)
        facts += [seal(f"n0:{len(facts) + 1}", "s0", "finite-effects", "n0", 0, len(facts))]
        final = sealed_query(facts, "finite-effects", iter(("n0",)))
        self.assertTrue(final.final)
        self.assertTrue(check_sealed(facts, "finite-effects", ("n0",)).accepted)
        ledger = compact_sealed_batch(facts, "finite-effects", iter(("n0",)))
        restored = BatchSummary.from_dict(ledger.summaries[0].as_dict())
        self.assertEqual(restored, ledger.summaries[0])
        aggregate = ledger.accounting()
        decoded = decode(facts).as_dict()
        self.assertEqual(aggregate, {key: value for key, value in decoded.items() if key != "witnesses"})


if __name__ == "__main__":
    unittest.main()
