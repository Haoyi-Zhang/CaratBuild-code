# CARAT research artifact

This directory is the executable artifact for *The Last-Fact Problem: Identity, Completion, and Evidence Retention in Federated Build Accounting*. It contains the implementation, differential checkers, deterministic tests, lawful public inputs, experiment drivers, and retained results.

Run the complete offline reproduction:

```bash
./scripts/reproduce.sh
```

The run executes the full unittest surface, all eighteen experiment campaigns, the finite boundary checker, collectors, provenance checks, environment capture, and fail-closed package audits. A nonzero exit status is a failed reproduction. The retained Linux execution in `results/current/` passes 72 tests with no skips, including eight module-level cases. It records 48.466 post-import stage CPU seconds and 76.004 MiB largest-worker RSS. Its twelve owned executable-DAG task records, 46-fact recovery union, and 205-fact raw export satisfy their checks. Historical failed checkpoint trials remain under `results/reproduction_failures/`; they are not overwritten by a later successful run.

The decoder builds effect unit-membership sets lazily once per uniquely defined referenced presentation within each call. The independent checker keeps its own per-effect scans; parsing, mint checks, counters and witness ordering are unchanged. Six self-contained pure-computation regressions in `tests/test_effect_membership.py` compare complete results to a test-local definition/atom-scan reference on the mint/presentation/effect/seal fragment, exercise transform controls with the independent checker, and check witness support and projection round trips. They need only the standard library:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -p test_effect_membership.py -v
```

Full discovery includes those six cases and the three in-process raw-export context cases in `tests/test_export_context.py`, so the current collector and final audit require exactly 81 tests, including at least eight module-level cases. They also require an actual successful run with zero failures, errors or skips. The earlier 78-test gate predates the three export cases; neither that label nor the retained 72-test Linux receipt is an 81-test execution. Historical campaign labels and results remain unchanged.

The gate's receipt/count regressions are kept outside `tests/` so they do not change the 81-case campaign surface. They count discovery without running it and require inclusion of all three export cases:

```bash
python3 -B scripts/test_unit_test_contract.py
PYTHONPATH=src python3 -B -m unittest discover -s tests -p test_export_context.py -v
```

These bounded checks do not rerun services, crash recovery, external build tasks or measurements, and do not establish a complete 81-test Linux reproduction.

The evaluated model is a finite fixed faithful-origin roster, crash-stop endpoints, bounded local resources, and eventual delivery after the final failure. Projection retains an exact bounded replay index so old facts are idempotent, visible conflicts remain rejectable, and disjoint later batches can continue. The artifact does not claim Byzantine security, dynamic membership, WAN performance, cryptographic provenance, or production deployment.
