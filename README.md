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

Full discovery includes six effect-membership cases, three raw-export context cases, two lossless-storage comparison cases, three complete-stream controls, five same-contract local lifecycle controls and four lifecycle-driver controls. The current collector requires exactly 95 tests, including at least eight module-level cases, with zero failures, errors or skips. The retained Linux receipt covers its 72-test source surface; it is not an execution of the expanded suite.

The gate's receipt/count regressions are kept outside `tests/` so they do not change the 95-case campaign surface. They count discovery without running it and require inclusion of all three export cases:

```bash
python3 -B scripts/test_unit_test_contract.py
PYTHONPATH=src python3 -B -m unittest discover -s tests -p test_export_context.py -v
```

These bounded checks exercise their named code paths without rerunning the service and process campaigns.
The later-batch export regression now also binds the manifest's batch, target
seal IDs, and target/predecessor counts to the returned canonical facts. The
same method drives the complete collector across multiple in-process pages and
rejects noncanonical fact text even if the manifest byte total is adjusted.
It does not authenticate the remote endpoint. The retained 72-test Linux receipt predates
this guard and is not a new run of the expanded suite.
The scientific workflow runs these three cases in normal and optimized Python
on pushes, pull requests and manual dispatch; its complete reproduction remains
a separate gate for main-branch pushes, pull requests and manual dispatch.

## Lossless storage comparison

```bash
python3 -B scripts/compare_storage.py --output results/storage-comparison.json
```

The six-size comparison uses identical canonical facts and full accounting after decoding each representation. At 80 batches, raw JSONL is 503,178 bytes, whole-log zlib is 37,593, per-batch zlib with base-85/JSON framing is 77,606, and projection is 154,644. Each restores all facts. Projection therefore neither erases information nor establishes superior compression. Raw-serving holder obligations and completion checks are separate from encoding size. Self-contained windows admit disjoint fresh identities; reused work spanning windows requires a different accounting contract.

For checked query and local lifecycle costs, use a fresh disjoint work/result pair:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B scripts/compare_lifecycle.py \
  --work /fresh/private/work --output /fresh/lifecycle-results
```

The native SQLite/zlib comparator uses the unchanged owned two-window retention
fixture, the same identity/progress evidence, and three raw copies/receipts.
Both paths match all accounting/progress queries, replay and later admission,
and reconstruct all twenty facts after reopening. `results/storage-lifecycle/`
retains the actual inputs, ten raw lifecycle samples, full local file totals,
phase costs and exact source/native-runtime bindings. No compression or speedup
claim is made from the small SQLite container comparison. See
`docs/storage-lifecycle.md` for costs, native/shared-code distinctions and
unmeasured scope. `results/stream-controls/` retains the complete-stream red/green
and selected-regression receipts; historical measurements are unchanged.

The lifecycle driver checks initial and later queries, replay/admission counts,
full reconstruction and paired outputs with explicit exceptions in both normal
and optimized Python. Four deterministic driver tests include rejecting a
disagreement before publishing samples or a success summary. The retained
ten timing samples bind their original driver; this guard repair does not
replace those observations or claim a new performance experiment.

The evaluated model is a finite fixed faithful-origin roster, crash-stop endpoints, bounded local resources, and eventual delivery after the final failure. Projection retains an exact bounded replay index so old facts are idempotent, visible conflicts remain rejectable, and disjoint later batches can continue. The artifact does not claim Byzantine security, dynamic membership, WAN performance, cryptographic provenance, or production deployment.
