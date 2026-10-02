# Artifact guide

## Scope

This artifact evaluates accounting-window finality and evidence retention for a fixed, faithful source roster under crash-stop endpoint failures and eventual delivery after the final failure. It does **not** implement Byzantine source validation, dynamic membership, cryptographic identity, WAN deployment, general distributed garbage collection, or production build attribution.

## One-command reproduction

```sh
./scripts/reproduce.sh
```

The command is offline, fail-closed, and stage-isolated. It runs the complete unittest surface (including module-level tests), six original campaigns, twelve boundary campaigns, the independent finite boundary checker, the collectors, public-input verification, environment/dependency capture, and final package audits. Any failed stage produces a nonzero status. Performance fields are descriptive and machine-dependent; semantic acceptance, sets, masses, safety decisions, and exact counts are the reproducibility target.

## Fast inspection

```sh
python proofs/boundary_model_check.py
python scripts/verify_public_inputs.py
python scripts/reviewer_audit.py
```

The boundary checker does not import the CARAT implementation. It checks the identity and last-fact indistinguishability witnesses, the general failure-family retention condition, its cardinality-`f` corollary, the exact-membership state lower bound, and namespace-ablation witnesses. The public-input verifier checks the packaged full pytest fix/backport diffs, their selected hunks, the license, and independent robustness snapshots. The reviewer audit validates the distributable code tree and generated results; manuscript bibliography checks are intentionally paper-side and are not an unconditional dependency of the standalone code artifact.

## Public and generated inputs

`inputs/public/pytest/` contains the exact upstream diff bytes for pytest PR 14018 and its declared backport PR 14074, the upstream MIT license, source locations, and a byte manifest. `external_inputs/pytest_pair.json` contains the six selected hunks consumed by the adapter. The adapter itself accepts any bounded three-file explicit-backport declaration whose two sides have equal path-indexed changed-line payloads; the packaged pytest pair is one evaluated instance, not a hard-coded whitelist or a representativeness claim. The three additional diffs in `inputs/public-patches/` are independent provenance/robustness fixtures and are not substitutes for the fix/backport pair.

## Projection and transport contract

Projection stores exact aggregate fields, identifier fences, retained seals, and a bounded compressed index of discarded canonical facts. An exact old replay is a no-op; a different fact reusing a discarded coordinate, an extra old-window seal, or a reused identifier is rejected. A disjoint later batch remains admissible. Pages, `put` requests, and raw export are bounded by actual encoded JSON bytes as well as fact count; raw export carries a manifest that the caller verifies before accepting completeness.

## Interpreting performance

Timing and resident-memory values vary with hardware, scheduling, filesystem state, and co-tenancy. The manuscript and result README must take CPU, RSS, decode time, and checker time from one retained complete run rather than mixing runs. Correctness arguments do not depend on those measurements.
