"""Pure receipt/count regressions; do not run the scientific campaigns.

Kept outside tests/ so these gate checks do not change its 81-case campaign
surface. Discovery below only counts existing cases; it does not run them.
"""
from __future__ import annotations

import copy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

from unit_test_contract import EXPECTED_TESTS, validate_current_unit_tests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EXPORT_CASES = {
    "test_export_context.ExportContextTests.test_genesis_package_has_no_support_context",
    "test_export_context.ExportContextTests.test_later_batch_pages_include_transitive_seals_not_earlier_data",
    "test_export_context.ExportContextTests.test_missing_toy_predecessor_is_not_silently_omitted",
}


def iter_cases(suite):
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            yield from iter_cases(value)
        else:
            yield value


class UnitTestContractTests(unittest.TestCase):
    def setUp(self):
        # A synthetic contract fixture, not a claimed full-suite execution.
        self.record = {"successful": True, "tests_run": EXPECTED_TESTS,
                       "module_level_tests_run": 8, "failures": 0,
                       "errors": 0, "skipped": 0}

    def test_current_successful_receipt_is_accepted_without_mutation(self):
        before = copy.deepcopy(self.record)
        validate_current_unit_tests(self.record)
        self.assertEqual(self.record, before)

    def test_discovery_counts_exact_suite_and_known_three_export_cases(self):
        loader = unittest.TestLoader()
        cases = list(iter_cases(loader.discover(str(ROOT / "tests"))))
        self.assertEqual(loader.errors, [])
        self.assertEqual(len(cases), EXPECTED_TESTS)
        self.assertEqual(sum(isinstance(case, unittest.FunctionTestCase) for case in cases), 8)
        self.assertTrue(EXPORT_CASES.issubset({case.id() for case in cases}))

    def test_historical_and_partial_counts_are_not_current_success(self):
        for count in (66, 72, 78, 80, 82, True, 81.0):
            with self.subTest(count=count):
                wrong = dict(self.record, tests_run=count)
                with self.assertRaises(AssertionError):
                    validate_current_unit_tests(wrong)

    def test_count_alone_cannot_mask_failed_skipped_or_incomplete_run(self):
        for field, value in (("successful", False), ("successful", 1),
                             ("failures", 1), ("errors", 1), ("skipped", 1),
                             ("failures", False), ("errors", 0.0),
                             ("module_level_tests_run", 7)):
            with self.subTest(field=field, value=value):
                wrong = dict(self.record, **{field: value})
                with self.assertRaises(AssertionError):
                    validate_current_unit_tests(wrong)
        for field in self.record:
            with self.subTest(missing=field):
                wrong = dict(self.record)
                wrong.pop(field)
                with self.assertRaises(AssertionError):
                    validate_current_unit_tests(wrong)

    def test_collector_and_audit_enforce_contract_before_later_work(self):
        spec = spec_from_file_location("ci_accounting_audit", ROOT / "scripts/audit_artifact.py")
        audit = module_from_spec(spec)
        spec.loader.exec_module(audit)
        self.assertIs(audit.validate_current_unit_tests, validate_current_unit_tests)
        complete = {"documented_commands_completed": True,
                    "scientific_completion": True, "venue_submission_ready": False}
        for receipt, accepted in (
            (self.record, True),
            (dict(self.record, tests_run=78), False),
            (dict(self.record, successful=False), False),
            (dict(self.record, skipped=1), False),
        ):
            error = RuntimeError if accepted else AssertionError
            with self.subTest(consumer="collector", receipt=receipt), \
                    patch.object(Path, "read_text", side_effect=[
                        json.dumps({}), json.dumps(receipt),
                        RuntimeError("campaign-summary boundary reached")]) as read, \
                    patch.object(Path, "write_text") as write:
                with self.assertRaises(error):
                    runpy.run_path(str(ROOT / "scripts/collect_all.py"))
                self.assertEqual(read.call_count, 3 if accepted else 2)
                write.assert_not_called()
            with self.subTest(consumer="audit", receipt=receipt), \
                    patch.object(audit, "load_json", side_effect=[complete, receipt]), \
                    patch.object(Path, "open", side_effect=RuntimeError("ledger boundary reached")) as opened, \
                    patch.object(Path, "write_text") as write:
                with self.assertRaises(error):
                    audit.main()
                self.assertEqual(opened.call_count, 1 if accepted else 0)
                write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
