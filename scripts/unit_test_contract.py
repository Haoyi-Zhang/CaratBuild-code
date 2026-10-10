"""Current-run test accounting, separate from retained campaign receipts."""
from __future__ import annotations

# 72 retained Linux tests + 6 effect-membership cases + 3 raw-export cases.
# This is a current gate, not a relabeling of any historical execution.
EXPECTED_TESTS = 81
MIN_MODULE_LEVEL_TESTS = 8


def validate_current_unit_tests(record):
    """Require an actual successful, complete, zero-skip current-run receipt."""
    if record.get("successful") is not True:
        raise AssertionError("the current deterministic test run did not succeed")
    if type(record.get("tests_run")) is not int or record["tests_run"] != EXPECTED_TESTS:
        raise AssertionError(f"the current deterministic suite requires exactly {EXPECTED_TESTS} tests")
    for field in ("failures", "errors", "skipped"):
        if type(record.get(field)) is not int or record[field] != 0:
            raise AssertionError(f"the current deterministic run requires zero {field}")
    module_count = record.get("module_level_tests_run")
    if type(module_count) is not int or module_count < MIN_MODULE_LEVEL_TESTS:
        raise AssertionError("the current deterministic run omitted module-level tests")
