from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=spec_from_file_location('reviewer_audit',ROOT/'scripts'/'reviewer_audit.py')
mod=module_from_spec(spec);assert spec.loader;spec.loader.exec_module(mod)

def test_distributable_tree_passes_offline_reviewer_audit():
    r=mod.audit(ROOT,require_generated=False)
    assert r['ok'],r['failures']


def load_tests(loader, tests, pattern):
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            tests.addTest(unittest.FunctionTestCase(value, description=name))
    return tests
