from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=spec_from_file_location('boundary_model_check',ROOT/'proofs'/'boundary_model_check.py')
mod=module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod)

def test_independent_boundary_model_check():
    out=mod.run()
    assert out['independent_of_implementation'] is True
    assert out['failure_family']['equivalence_cases']>0
    assert out['projection_namespace_ablation']['all_four_namespaces_necessary_for_this_exact_fence']

def test_exact_membership_bound_is_stated_with_universe():
    rows=mod.run()['exact_membership_state_lower_bound']['fixed_cardinality']
    assert all(r['minimum_state_bits']>=r['seen'] for r in rows)

def test_correlated_failures_defeat_count_only_rule():
    c=mod.run()['failure_family']['correlated_counterexample']
    assert len(c['same_size_unsafe_holders'])==len(c['same_size_safe_holders'])


def load_tests(loader, tests, pattern):
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            tests.addTest(unittest.FunctionTestCase(value, description=name))
    return tests
