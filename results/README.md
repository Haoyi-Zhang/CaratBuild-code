# Reproduction evidence

`raw/` contains per-case outcomes and negative evidence. `summary/` contains aggregate collectors and descriptive resource measurements. `reproduction_failures/` preserves the three inherited `returncode=1` trial summaries and one re-executed checkpoint failure transcript; the transcript establishes the captured failure stage for that replay only and does not retroactively invent stderr for the other two historical runs. A repaired clean run is recorded separately.

Semantic reproduction compares counts, sets, masses, decisions, witness acceptance, finality, crash survival, projection adjudication, and export completeness. Timings and operating-system memory high-water marks are descriptive and may differ across runs.

## Stage inventory

### Six original campaigns

1. `manipulation` — 720 generated valid histories;
2. `faults` — 160 five-replica coordinate-emulator runs;
3. `ambiguity` — 400 injected-invalid histories and witnesses;
4. `scale` — 18 direct in-memory rows;
5. `compaction` — six bulk projection sizes;
6. `exhaustive` — 960 tiny delivery schedules, 39 composed operation sequences, and a permanent-hole counterexample.

### Twelve boundary campaigns

1. `identity` — strongest scalar baseline over all 1,120 histories;
2. `transport` — coordinate-gap counterexamples plus 324 complete-page cases;
3. `service-pilot` — bounded endpoint pilot;
4. `service` — twelve framed five-endpoint campaigns;
5. `recovery` — whole-process termination and acknowledged-set reconstruction;
6. `compaction-boundaries` — projection, overlap, forged-summary, and replay decisions;
7. `public-pair` — nine adapter decisions and one service run;
8. `semantic-boundaries` — four deliberately accepted non-claims;
9. `sealed-windows` — two completion worlds, 32 arrival vectors, 32 future vectors, identifier reuse, five service phases, and sealed projection;
10. `multiprocess-retention` — five independent processes, partition/heal exchange, pre-heal restart, `f+1` certificates, crashes, paged raw export, projected restart, and replay/conflict adjudication;
11. `completion-boundaries` — identity worlds, ten support removals, six ablations, and Byzantine omission worlds;
12. `executable-build` — three local task-graph scenarios.

The final collector combines all eighteen campaigns with the complete test outcome. The artifact audit checks code-side claim paths, input provenance, retained network evidence, and package hygiene. Manuscript reference auditing is performed on the paper package, not by an absent code-side `reference_audit.csv`.

## Principal recorded results

The following semantic results remain claim-critical and are asserted by the final collector:

- 720 valid and 400 invalid histories; unique-mint mass exact in all 1,120;
- 400 integrity refusals and 400 verified witnesses;
- occurrence-based amplification reaches 17× while unique-mint mass remains 1×;
- identity observation identical with oracle masses 5 and 10;
- completion observation identical with complete mass 10 or delayed mass 5;
- exactly one final arrival vector among 32; all 32 admissible future subsets preserve mass 15;
- ten support removals refused and six weakened-condition counterexamples;
- five processes and logs converge in three page-relay rounds after two partitioned rounds and a pre-heal restart;
- three holders at `f=2`, all 16 crash sets, two holder kills, and a 205-fact paged survivor export;
- exact projected raw replay is idempotent, while an altered fact at the same deleted coordinate is rejected; projected restart preserves mass 600;
- a mixed page containing old replay and a valid later batch advances and admits the later batch;
- a 32-fact JSON page larger than 1 MiB is split by encoded bytes; bounded export verifies stable manifests, returned offsets, an advancing cursor, count, canonical bytes, and sealed completeness, but does not separately bind manifest batch/seal-ID fields;
- all 324 complete-page cases converge; retained coordinate-gap cases do not;
- twelve service campaigns reach exact union, with six common acceptances and six common refusals;
- one process kill preserves 46 acknowledged fact occurrences and reaches the 46-fact union;
- the executable graph records twelve outcomes from nine subprocesses: seven pass, two fail, three skip.

Projection byte counts and all descriptive timing/resource measurements belong to the retained clean run and must be read from `raw/compaction.csv`, `raw/multiprocess_retention_summary.json`, `raw/scale.csv`, and `summary/complete_overview.json`. That run covered 66 tests (eight module-level); the current suite has 72 after six scientific regressions. New sidecar checks do not replace or relabel these historical host measurements.

## Resource scope

`summary/complete_overview.json` records post-import stage CPU and the largest single-worker resident-memory high-water mark for one complete run. `environment.json` records the corresponding execution environment. These measurements exclude manuscript work and historical engineering and are not concurrent aggregate RSS. Clean reproduction is required to preserve semantic decisions and counts, not host-specific timings.

## Current clean-copy record

The manuscript's resource and latency values all come from the single successful run recorded in `final_clean_reproduction.json` and `reproduction_logs/clean-offline-*`.  Historical `returncode=1` records remain separate in `independent_reproduction_trials.json` and `reproduction_failures/`; they are not overwritten or reinterpreted as timing variance.
