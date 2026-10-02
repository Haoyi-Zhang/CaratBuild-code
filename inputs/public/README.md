# Public inputs

The exact source/backport case study is retained under `pytest/`:

- `pr-14018.diff` and `pr-14074.diff` are the complete public pull-request diffs;
- `LICENSE.pytest` is the upstream MIT license text;
- `PROVENANCE.json` records source URLs, byte counts, SHA-256 digests, and the exact selected paths;
- `external_inputs/pytest_pair.json` contains the six selected hunks consumed by the adapter.

The verifier checks that every selected hunk occurs verbatim in its corresponding complete diff. The adapter itself is not a pytest-path whitelist: it accepts any explicit source/target declaration satisfying the bounded three-file format and equal path-indexed changed-line payload contract. The packaged pytest pair is the one evaluated public instance. Generated scale parameters and adversarial histories are not described as public repository histories.
