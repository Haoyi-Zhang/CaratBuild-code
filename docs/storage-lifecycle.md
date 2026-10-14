# Same-contract local native storage comparison

`scripts/compare_lifecycle.py` compares the actual sealed projection/summary
loader with a native SQLite identity index and per-batch zlib BLOB logs. It
uses the unchanged owned `tests/test_retention.py:two_window_fixture`: ten
initial facts and ten later facts. Both receive the same declared IDs, exact
roster/interval progress evidence and three raw-holder receipts. The ordinary
SQLite path updates only new index rows and touched batch logs on append.

Both paths produce identical six-field accounting and batch finality queries,
recognize exact replay as a no-op, refuse identity/coordinate conflicts, admit
the fresh later window and reconstruct all canonical fact text after reopening.
Cross-window reference to old work remains undetermined/nonfinal rather than
being reminted. SQLite executes native indexing/count queries; mass addition
uses Python integers to preserve the existing arbitrary-width contract. Both
reuse CARAT's public normalizer and semantic progress checks. This is an actual
native storage/query path, not an independent validator or distributed service.

## Fresh run

```sh
PYTHONDONTWRITEBYTECODE=1 python -B scripts/compare_lifecycle.py \
  --work /fresh/private/work --output /fresh/scientific/results
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover \
  -s tests -p test_storage_lifecycle.py -v
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover \
  -s tests -p test_replay_stream.py -v
```

Both paths must be fresh and disjoint. The optional `CARAT_WORK_ROOT` environment
variable places unittest temporary directories inside the caller's assigned
workspace. Do not overwrite retained measurement directories. The full Linux
driver discovers these eight new methods through its existing test stage; its
91-test current gate is not a receipt that all 91 have run on this Windows host.

## Literal costs and null interpretation

`results/storage-lifecycle/input.json` retains the exact arrivals.
`samples.json` records ten complete lifecycles (five alternating repetitions
per mode), every query/reconstruction output, certificate, local file
inventory and phase CPU/wall costs. `summary.json` and `bindings.json` give the
scope, native runtime versions and exact nine-source/input bindings.

Phases include input read/parse, creation/synchronization of three actual raw
copies and receipt files, construction/compression/indexing/commit, two
close/reopens with reconstruction/validation, five initial and five later
checked queries, replay, fresh later append/commit, final reconstruction and
query verification, and close. Both retain equivalent evidence. SQLite uses
DELETE journaling and synchronous FULL; the projection wrapper synchronizes
files before replacement. This experiment tests close/reopen, not injected
process crashes, directory durability or power loss; the five-process crash
experiment remains its separate historical evidence.

Archive bytes are 3,162 / 32,768 (projection / SQLite). Including raw-holder
copies and receipts gives 6,762 / 36,368 actual file bytes. SQLite's page floor
and indexes dominate this small control, so these are not compression-ratio
claims or evidence against the ordinary whole-log/per-batch zlib results.
Bytes are logical persistent-file lengths, not allocated filesystem blocks or
directory metadata.
Whole-lifecycle wall medians are 46.50 / 47.88 ms; no speedup is inferred.
Imports/startup, fixture generation, experiment packaging, network, OS cold-cache
control, memory/transient peak storage and distributed failure execution are
excluded explicitly. CPU observations are retained without claiming resolution
finer than the host supplies. The result is a functional null: ordinary declared
identity, progress and compressed logs can serve these same queries.

## Complete compressed fields

The replay decoder requires `eof` and empty `unused_data`, besides its existing
32 MiB output bound, strict canonical fact parsing and aggregate/index equality.
Three owned controls preserve complete streams at native levels 0/1/9 and
reject a missing checksum trailer or a second/trailing stream. Native stream
completion is not authentication. `results/stream-controls/codec-pair.json`
retains six literal owned inputs and fresh paired old/current decoder outcomes;
the original red and current green test receipts are separate records.
See the [Python zlib API](https://docs.python.org/3.12/library/zlib.html)
and [zlib manual](https://www.zlib.net/manual.html) for the completion distinction.
The [SQLite PRAGMA documentation](https://www.sqlite.org/pragma.html) specifies
the recorded journaling, synchronization and page-size settings. No downloaded
engine, external target, exploit payload or upstream build is executed.
