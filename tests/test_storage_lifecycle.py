"""Same-information query/storage controls on the existing two-window fixture."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from carat.facts import mint, presentation
from carat.retention import certify, issue_receipt
from carat.storage_lifecycle import NAMES, ProjectionArchive, SQLiteArchive, canonical
from test_retention import two_window_fixture


class StorageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get('CARAT_WORK_ROOT'))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first, self.second, self.facts = two_window_fixture()
        self.initial = [fact for fact in self.facts if fact['batch'] == self.first]

    def stores(self, initial=None):
        values = self.initial if initial is None else initial
        certificate = certify([issue_receipt(values, self.first, NAMES, name, 2) for name in NAMES[:3]])
        output = []
        for kind, cls in (('projection', ProjectionArchive), ('sqlite', SQLiteArchive)):
            store = cls.create(self.root / kind, values, [certificate])
            self.addCleanup(store.close)
            output.append(store)
        return output

    def test_native_query_and_complete_reconstruction_match_projection(self):
        projection, native = self.stores()
        self.assertEqual(projection.query(), native.query())
        self.assertEqual(projection.reconstruct(), native.reconstruct())
        self.assertEqual(native.query()['accounting']['gross_mass'], 5)
        self.assertTrue(native.query()['progress'][self.first]['final'])

    def test_exact_replay_is_noop_and_conflict_is_atomic(self):
        stores = self.stores()
        for store in stores:
            before = store.query()
            self.assertEqual(store.admit(self.initial), 0)
            altered = copy.deepcopy(self.initial[0])
            altered['mass'] += 1
            with self.assertRaises(ValueError):
                store.admit([mint('n0:7', 'fresh-atomic', 1, 'n0', self.second), altered])
            self.assertEqual(store.query(), before)
            self.assertEqual(store.reconstruct(), canonical(self.initial))

    def test_later_window_and_reopen_preserve_both_query_contracts(self):
        stores = self.stores()
        later = [fact for fact in self.facts if fact['batch'] == self.second]
        for store in stores:
            self.assertEqual(store.admit(later), 10)
            answer = store.query()
            self.assertEqual(answer['accounting']['gross_mass'], 10)
            self.assertTrue(answer['progress'][self.second]['final'])
            store.close()
            reopened = type(store).open(store.path)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.query(), answer)
            self.assertEqual(reopened.reconstruct(), canonical(self.facts))

    def test_cross_window_old_work_is_not_reminted_or_borrowed(self):
        stores = self.stores()
        borrowed = presentation('n0:7', 'fresh-later-p', [('u', 1)], 'later', 0, self.second)
        for store in stores:
            self.assertEqual(store.admit([borrowed]), 1)
            answer = store.query()
            self.assertFalse(answer['accounting']['determined'])
            self.assertFalse(answer['progress'][self.second]['final'])
        self.assertEqual(stores[0].query(), stores[1].query())

    def test_arbitrary_width_mass_and_native_index_binding(self):
        initial = copy.deepcopy(self.initial)
        initial[0]['mass'] = 2 ** 80 + 5
        projection, native = self.stores(initial)
        self.assertEqual(projection.query(), native.query())
        self.assertEqual(native.query()['accounting']['gross_mass'], 2 ** 80 + 5)
        with native.db:
            native.db.execute("UPDATE records SET mass='6' WHERE kind='mint'")
        with self.assertRaisesRegex(ValueError, 'index differs'):
            native.query()


if __name__ == '__main__':
    unittest.main()
