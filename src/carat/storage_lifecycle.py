"""Local same-contract archives: CARAT projection and native SQLite + zlib.

This is a storage/query comparator, not an independent protocol or a new
distributed service. Both reuse the declared normalizer and semantic checks.
SQLite executes indexing/count queries; arbitrary-width mass addition stays
in Python. Both retain the exact same facts, seals and retention certificates.
"""
from collections import defaultdict
import json
import os
from pathlib import Path
import sqlite3
import zlib

from .closure import sealed_query
from .compaction import BatchSummary, CompactedLedger, compact_sealed_batch
from .decode import decode
from .facts import canonical_pair, parse_text
from .independent_check import check_sealed
from .retention import RetentionCertificate, validate_certificate

FIELDS = ('unique_units', 'gross_mass', 'presentations', 'effects', 'passing_effects', 'failing_effects')
NAMES = tuple(f'n{i}' for i in range(5))
MAX_BYTES = 32 * 1024 * 1024


def canonical(values):
    return tuple(sorted({canonical_pair(value)[0] for value in values}))


def persisted_json(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    temporary = path.with_suffix(path.suffix + '.next')
    with temporary.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    # Local process close/reopen only; no directory-sync or power-loss claim.


def inflate_exact(blob):
    inflater = zlib.decompressobj()
    raw = inflater.decompress(blob, MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or inflater.unconsumed_tail:
        raise ValueError('compressed log exceeds bound')
    raw += inflater.flush()
    if len(raw) > MAX_BYTES or not inflater.eof or inflater.unused_data:
        raise ValueError('compressed log must be one complete bounded stream')
    return raw


def identity_row(text):
    fact = parse_text(text)
    kind = fact['kind']
    field = {'mint': 'unit', 'presentation': 'presentation', 'transform': 'transform',
             'effect': 'effect', 'seal': 'seal'}[kind]
    return (fact['event'], kind, fact[field], fact['batch'],
            str(fact['mass']) if kind == 'mint' else None,
            fact['outcome'] if kind == 'effect' else None)


def admitted(existing, values, closed):
    """Exact replay/no-op and projected-window freshness; no payload inference."""
    current = set(existing)
    events = {identity_row(text)[0]: text for text in current}
    identifiers = {(identity_row(text)[1], identity_row(text)[2]): text for text in current}
    fresh = []
    for text in canonical(values):
        if text in current:
            continue
        event, kind, identifier, batch, _mass, _outcome = identity_row(text)
        if batch in closed or event in events or (kind, identifier) in identifiers:
            raise ValueError('closed window or globally scoped identity conflict')
        current.add(text)
        events[event] = text
        identifiers[kind, identifier] = text
        fresh.append(text)
    return tuple(fresh)


class Archive:
    def validate_certificates(self):
        for certificate in self.certificates:
            validate_certificate(certificate, NAMES, NAMES)
            seals = tuple(sorted(parse_text(text)['seal'] for text in self.facts
                                 if parse_text(text)['kind'] == 'seal'
                                 and parse_text(text)['batch'] == certificate.batch))
            if seals != certificate.seal_ids:
                raise ValueError('archive certificate differs from retained seals')
            closed = sealed_query(self.facts, certificate.batch, NAMES)
            checked = check_sealed(self.facts, certificate.batch, NAMES)
            if not closed.final or not checked.accepted or (
                    closed.unique_units, closed.gross_mass) != (checked.unique_units, checked.gross_mass):
                raise ValueError('archive closed window is not complete')

    def progress(self):
        # Identical ordinary progress evidence, including predecessor context.
        self.validate_certificates()
        output = {}
        for batch in sorted({parse_text(text)['batch'] for text in self.facts}):
            primary = sealed_query(self.facts, batch, NAMES)
            checked = check_sealed(self.facts, batch, NAMES)
            if (primary.final, primary.unique_units, primary.gross_mass) != (
                    checked.accepted, checked.unique_units, checked.gross_mass):
                raise ValueError('progress paths disagree')
            output[batch] = {'final': primary.final, 'unique_units': primary.unique_units,
                             'gross_mass': primary.gross_mass}
        return output

    @property
    def closed(self):
        return {certificate.batch for certificate in self.certificates}

    def reconstruct(self):
        return self.facts


class ProjectionArchive(Archive):
    @classmethod
    def create(cls, path, values, certificates):
        path = Path(path)
        if path.exists():
            raise ValueError('refuse existing projection archive')
        obj = cls.__new__(cls)
        obj.path = path
        obj.facts = canonical(values)
        obj.certificates = tuple(certificates)
        if len(obj.closed) != len(obj.certificates):
            raise ValueError('duplicate closed window')
        obj.validate_certificates()
        live = obj.facts
        summaries = []
        for batch in sorted(obj.closed):
            ledger = compact_sealed_batch(live, batch, NAMES)
            summaries.extend(ledger.summaries)
            live = ledger.live_facts
        obj.ledger = CompactedLedger(tuple(summaries), live)
        obj.save()
        return obj

    @classmethod
    def open(cls, path):
        obj = cls.__new__(cls)
        obj.path = Path(path)
        payload = json.loads(obj.path.read_text(encoding='utf-8'))
        obj.certificates = tuple(RetentionCertificate.from_dict(c) for c in payload['certificates'])
        obj.ledger = CompactedLedger(tuple(BatchSummary.from_dict(s) for s in payload['ledger']['summaries']),
                                     tuple(payload['ledger']['live_facts']))
        if {s.batch for s in obj.ledger.summaries} != obj.closed or not obj.ledger.boundary_valid():
            raise ValueError('projection archive has invalid boundary')
        obj.facts = tuple(sorted(set(obj.ledger.live_facts).union(
            text for summary in obj.ledger.summaries for text in summary.replay_facts)))
        obj.validate_certificates()
        return obj

    def save(self):
        persisted_json(self.path, {'ledger': self.ledger.as_dict(),
                                  'certificates': [c.as_dict() for c in self.certificates]})

    def query(self):
        return {'accounting': self.ledger.accounting(), 'progress': self.progress()}

    def admit(self, values):
        fresh = admitted(self.facts, values, self.closed)
        if fresh:
            candidate = CompactedLedger(self.ledger.summaries, tuple(sorted(set(self.ledger.live_facts) | set(fresh))))
            if not candidate.boundary_valid():
                raise ValueError('projection admission violates boundary')
            self.ledger = candidate
            self.facts = tuple(sorted(set(self.facts) | set(fresh)))
            self.save()
        return len(fresh)

    def close(self):
        pass


class SQLiteArchive(Archive):
    @classmethod
    def create(cls, path, values, certificates):
        path = Path(path)
        if path.exists():
            raise ValueError('refuse existing SQLite archive')
        obj = cls.__new__(cls)
        obj.path = path
        obj.facts = canonical(values)
        obj.certificates = tuple(certificates)
        if len(obj.closed) != len(obj.certificates):
            raise ValueError('duplicate closed window')
        obj.validate_certificates()
        obj.db = sqlite3.connect(path)
        obj.db.execute('PRAGMA journal_mode=DELETE')
        obj.db.execute('PRAGMA synchronous=FULL')
        obj.db.executescript('''
            CREATE TABLE records(event TEXT PRIMARY KEY, kind TEXT, identifier TEXT,
                                 batch TEXT, mass TEXT, outcome TEXT, UNIQUE(kind,identifier));
            CREATE TABLE logs(batch TEXT PRIMARY KEY, payload BLOB NOT NULL);
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        obj.save()
        return obj

    @classmethod
    def open(cls, path):
        obj = cls.__new__(cls)
        obj.path = Path(path)
        if not obj.path.is_file():
            raise ValueError('missing SQLite archive')
        obj.db = sqlite3.connect(obj.path)
        obj.db.execute('PRAGMA synchronous=FULL')
        try:
            raw = obj.db.execute("SELECT value FROM settings WHERE key='certificates'").fetchone()[0]
            obj.certificates = tuple(RetentionCertificate.from_dict(c) for c in json.loads(raw))
            texts = []
            for batch, blob in obj.db.execute('SELECT batch,payload FROM logs ORDER BY batch'):
                recovered = inflate_exact(blob).decode('utf-8').splitlines()
                if any(parse_text(text)['batch'] != batch for text in recovered):
                    raise ValueError('compressed batch binding differs')
                texts.extend(recovered)
            obj.facts = canonical(texts)
            if tuple(sorted(texts)) != obj.facts:
                raise ValueError('compressed facts are duplicate or noncanonical')
            obj.audit_index()
            obj.validate_certificates()
        except Exception:
            obj.db.close()
            raise
        return obj

    def save(self):
        groups = defaultdict(list)
        for text in self.facts:
            groups[parse_text(text)['batch']].append(text)
        with self.db:
            self.db.execute('DELETE FROM records')
            self.db.execute('DELETE FROM logs')
            self.db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?)', [identity_row(text) for text in self.facts])
            self.db.executemany('INSERT INTO logs VALUES(?,?)',
                                [(batch, zlib.compress('\n'.join(texts).encode('utf-8'), 9))
                                 for batch, texts in sorted(groups.items())])
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                            ('certificates', json.dumps([c.as_dict() for c in self.certificates], sort_keys=True)))

    def audit_index(self):
        expected = {identity_row(text) for text in self.facts}
        actual = set(self.db.execute('SELECT event,kind,identifier,batch,mass,outcome FROM records'))
        if actual != expected:
            raise ValueError('native index differs from compressed facts')

    def query(self):
        self.audit_index()
        live = [text for text in self.facts if parse_text(text)['batch'] not in self.closed]
        diagnostic = decode(live)
        # Like projection, later live references cannot borrow closed definitions.
        if not diagnostic.determined:
            accounting = {'determined': False, **{f: getattr(diagnostic, f) for f in FIELDS}}
        else:
            counts = dict(self.db.execute('SELECT kind,COUNT(*) FROM records GROUP BY kind'))
            outcomes = dict(self.db.execute("SELECT outcome,COUNT(*) FROM records WHERE kind='effect' GROUP BY outcome"))
            accounting = {'determined': True, 'unique_units': counts.get('mint', 0),
                          'gross_mass': sum(int(row[0]) for row in self.db.execute("SELECT mass FROM records WHERE kind='mint'")),
                          'presentations': counts.get('presentation', 0), 'effects': counts.get('effect', 0),
                          'passing_effects': outcomes.get('pass', 0), 'failing_effects': outcomes.get('fail', 0)}
        return {'accounting': accounting, 'progress': self.progress()}

    def admit(self, values):
        fresh = admitted(self.facts, values, self.closed)
        if fresh:
            candidate = tuple(sorted(set(self.facts) | set(fresh)))
            touched = {parse_text(text)['batch'] for text in fresh}
            # Ordinary incremental update: do not recompress closed old logs
            # or rebuild their identity index when appending a fresh window.
            with self.db:
                self.db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?)',
                                    [identity_row(text) for text in fresh])
                for batch in sorted(touched):
                    rows = [text for text in candidate if parse_text(text)['batch'] == batch]
                    self.db.execute('INSERT OR REPLACE INTO logs VALUES(?,?)',
                                    (batch, zlib.compress('\n'.join(rows).encode('utf-8'), 9)))
            self.facts = candidate
        return len(fresh)

    def close(self):
        self.db.close()
