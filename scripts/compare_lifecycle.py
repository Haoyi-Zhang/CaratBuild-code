"""Fresh bounded same-contract local lifecycle, using an unchanged owned fixture."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
from carat.facts import canonical_text
from carat.retention import certify, issue_receipt
from carat.storage_lifecycle import NAMES, ProjectionArchive, SQLiteArchive, canonical, persisted_json
from test_retention import two_window_fixture


def write_new(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def trial(mode, work, input_path, queries):
    work.mkdir(exist_ok=False)
    phases = {}
    started = time.perf_counter(), time.process_time()
    def phase(name, action):
        before = time.perf_counter(), time.process_time()
        try:
            return action()
        finally:
            phases[name] = {'wall_seconds': time.perf_counter() - before[0],
                            'cpu_seconds': time.process_time() - before[1]}
    payload = phase('input_read_parse', lambda: json.loads(input_path.read_text(encoding='utf-8')))
    first, second = payload['batches']
    initial, later = payload['initial'], payload['later']
    def holders():
        receipts = []
        for holder in NAMES[:3]:
            receipt = issue_receipt(initial, first, NAMES, holder, 2)
            receipts.append(receipt)
            # Actual synchronized raw-copy and receipt files, common to both.
            with (work / (holder + '-raw.jsonl')).open('xb') as handle:
                handle.write(('\n'.join(canonical(initial)) + '\n').encode('utf-8'))
                handle.flush()
                os.fsync(handle.fileno())
            persisted_json(work / (holder + '-receipt.json'), receipt.as_dict())
        return certify(receipts)
    certificate = phase('raw_holders_receipts', holders)
    cls = ProjectionArchive if mode == 'projection' else SQLiteArchive
    path = work / ('archive.json' if mode == 'projection' else 'archive.sqlite')
    store = None
    try:
        store = phase('build_commit', lambda: cls.create(path, initial, [certificate]))
        def reopen():
            store.close()
            return cls.open(path)
        store = phase('close_reopen_initial', reopen)
        initial_answers = phase('queries_initial', lambda: [store.query() for _ in range(queries)])
        expected_initial = {'determined': True, 'unique_units': 1, 'gross_mass': 5,
                            'presentations': 2, 'effects': 1, 'passing_effects': 1, 'failing_effects': 0}
        assert all(answer['accounting'] == expected_initial and answer['progress'][first]['final']
                   for answer in initial_answers)
        replay_added = phase('replay', lambda: store.admit(initial))
        assert replay_added == 0
        appended = phase('append_commit', lambda: store.admit(later))
        assert appended == 10
        later_answers = phase('queries_later', lambda: [store.query() for _ in range(queries)])
        assert all(answer['accounting']['gross_mass'] == 10 and answer['accounting']['unique_units'] == 6
                   and answer['progress'][first]['final'] and answer['progress'][second]['final']
                   for answer in later_answers)
        store = phase('close_reopen_later', reopen)
        def final_verify():
            recovered = store.reconstruct()
            assert recovered == canonical(initial + later)
            answer = store.query()
            assert answer == later_answers[0]
            return {'canonical_facts': list(recovered), 'query': answer}
        recovery = phase('reconstruct_and_query_verify', final_verify)
        native_pragmas = None
        if mode == 'sqlite':
            native_pragmas = {key: store.db.execute('PRAGMA ' + key).fetchone()[0]
                              for key in ('journal_mode', 'synchronous', 'page_count', 'page_size')}
        phase('close', store.close)
        store = None
    finally:
        if store is not None:
            store.close()
    total = {'wall_seconds': time.perf_counter() - started[0],
             'cpu_seconds': time.process_time() - started[1]}
    files = {p.name: p.stat().st_size for p in sorted(work.iterdir()) if p.is_file()}
    return {'mode': mode, 'phases': phases, 'total': total,
            'file_bytes': files, 'all_endpoint_and_holder_bytes': sum(files.values()),
            'archive_bytes': files[path.name], 'native_pragmas': native_pragmas,
            'certificate': certificate.as_dict(), 'initial_queries': initial_answers,
            'later_queries': later_answers, 'replay_added': replay_added, 'appended': appended,
            'recovery': recovery}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--queries', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 7 or not 1 <= args.queries <= 10:
        parser.error('bounded repeats 1..7 and queries 1..10 required')
    work, out = args.work.resolve(), args.output.resolve()
    if work == out or work in out.parents or out in work.parents:
        parser.error('work and output must be fresh disjoint directories')
    work.mkdir(parents=True, exist_ok=False)
    out.mkdir(parents=True, exist_ok=False)
    first, second, facts = two_window_fixture()
    payload = {'recipe': 'unchanged tests/test_retention.py two_window_fixture',
               'batches': [first, second],
               'initial': [canonical_text(f) for f in facts if f['batch'] == first],
               'later': [canonical_text(f) for f in facts if f['batch'] == second]}
    write_new(work / 'input.json', payload)
    write_new(out / 'input.json', payload)
    samples = []
    for repeat in range(args.repeats):
        order = ('projection', 'sqlite') if repeat % 2 == 0 else ('sqlite', 'projection')
        pair = []
        for mode in order:
            row = trial(mode, work / f'{repeat}-{mode}', work / 'input.json', args.queries)
            row.update(repeat=repeat, order=list(order))
            samples.append(row)
            pair.append(row)
        assert pair[0]['initial_queries'] == pair[1]['initial_queries']
        assert pair[0]['later_queries'] == pair[1]['later_queries']
        assert pair[0]['recovery'] == pair[1]['recovery']
    sources = ('src/carat/storage_lifecycle.py', 'src/carat/compaction.py', 'src/carat/closure.py',
               'src/carat/facts.py', 'src/carat/decode.py', 'src/carat/independent_check.py',
               'src/carat/retention.py', 'tests/test_retention.py', 'scripts/compare_lifecycle.py')
    write_new(out / 'samples.json', {'samples': samples})
    summary = {'trial_count': len(samples), 'queries_per_phase': args.queries,
               'initial_facts': len(payload['initial']), 'later_facts': len(payload['later']),
               'same_query_and_reconstruction': True, 'native_distributed_service': False,
               'memory_and_transient_peak_bytes': None,
               'scope': 'one local query archive plus three synchronized first-window raw copies/receipts; create, commit, checked queries, replay, later append, close/reopen and full reconstruction',
               'exclusions': 'process/import startup, fixture generation, experiment packaging, OS cold-cache control, network, power loss and distributed crash testing',
               'modes': {mode: {'archive_bytes': sorted({s['archive_bytes'] for s in samples if s['mode'] == mode}),
                                'all_endpoint_and_holder_bytes': sorted({s['all_endpoint_and_holder_bytes'] for s in samples if s['mode'] == mode}),
                                'median_total_wall_seconds': statistics.median(s['total']['wall_seconds'] for s in samples if s['mode'] == mode),
                                'median_total_cpu_seconds': statistics.median(s['total']['cpu_seconds'] for s in samples if s['mode'] == mode)}
                         for mode in ('projection', 'sqlite')}}
    write_new(out / 'summary.json', summary)
    write_new(out / 'bindings.json', {'source_sha256': {f: sha256((ROOT / f).read_bytes()).hexdigest() for f in sources},
               'input_sha256': sha256((out / 'input.json').read_bytes()).hexdigest(),
               'environment': {'python': sys.version, 'platform': platform.platform(),
                               'sqlite_runtime': sqlite3.sqlite_version, 'zlib_runtime': zlib.ZLIB_RUNTIME_VERSION,
                               'bytecode_disabled': sys.dont_write_bytecode},
               'command_contract': 'python -B scripts/compare_lifecycle.py --work <fresh-private-work> --output <fresh-results>'})
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()
