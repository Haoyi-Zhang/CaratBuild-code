import hashlib,json
from pathlib import Path
import shutil
import tempfile
import unittest

from carat.public_inputs import verify_pytest_pair

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'inputs'/'public'/'pytest'

def test_public_input_bytes_match_provenance_manifest():
    manifest=json.loads((P/'PROVENANCE.json').read_text())
    by_name={x['file']:x for x in manifest['files']}
    for name in ('pr-14018.diff','pr-14074.diff','LICENSE.pytest'):
        b=(P/name).read_bytes()
        assert len(b)==by_name[name]['bytes']
        assert hashlib.sha256(b).hexdigest()==by_name[name]['sha256']

def test_public_diffs_identify_exact_upstream_prs_and_expected_fix():
    a=(P/'pr-14018.diff').read_text(errors='replace')
    b=(P/'pr-14074.diff').read_text(errors='replace')
    for diff in (a,b):
        assert 'conftest.py files are not plugins' in diff
        assert 'Blocking conftest files using -p is not supported' in diff
        assert 'testing/test_config.py' in diff
        assert 'src/_pytest/config/__init__.py' in diff

def test_upstream_license_is_packaged_for_offline_review():
    license_text=(P/'LICENSE.pytest').read_text(errors='replace')
    assert 'MIT License' in license_text or 'Permission is hereby granted' in license_text


def test_missing_manifest_fails_the_actual_offline_verifier():
    with tempfile.TemporaryDirectory() as temporary:
        copied = Path(temporary) / "pytest"
        shutil.copytree(P, copied)
        (copied / "PROVENANCE.json").unlink()
        errors = verify_pytest_pair(copied)
        assert errors and "missing provenance manifest" in errors[0]


def load_tests(loader, tests, pattern):
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            tests.addTest(unittest.FunctionTestCase(value, description=name))
    return tests
