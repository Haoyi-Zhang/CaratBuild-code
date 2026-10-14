"""Owned compressed-summary controls: complete text is not a complete stream."""
import base64
import copy
import unittest
import zlib

from carat.compaction import BatchSummary, compact_closed_batches
from carat.facts import mint, presentation, effect


def summary_record():
    facts = [mint('n0:1', 'owned-u', 5, 'n0', 'owned'),
             presentation('n0:2', 'owned-p', [('owned-u', 1)], 'main', 0, 'owned'),
             effect('n0:3', 'owned-e', 'owned-p', 'owned-u', 'pass', 'prepare', 'owned')]
    return compact_closed_batches(facts, ['owned']).summaries[0].as_dict()


class ReplayStreamTests(unittest.TestCase):
    def test_complete_native_stream_and_alternative_level_roundtrip(self):
        record = summary_record()
        expected = BatchSummary.from_dict(record)
        raw = zlib.decompress(base64.b85decode(record['replay_blob']))
        for level in (0, 1, 9):
            alternative = copy.deepcopy(record)
            alternative['replay_blob'] = base64.b85encode(zlib.compress(raw, level)).decode('ascii')
            self.assertEqual(BatchSummary.from_dict(alternative), expected)

    def test_missing_native_checksum_trailer_is_rejected(self):
        record = summary_record()
        blob = base64.b85decode(record['replay_blob'])
        # A benign incomplete write of our own native-compressor output:
        # all fact bytes are available, but stream completion is not.
        decoder = zlib.decompressobj()
        recovered = decoder.decompress(blob[:-4])
        self.assertEqual(recovered, zlib.decompress(blob))
        self.assertFalse(decoder.eof)
        record['replay_blob'] = base64.b85encode(blob[:-4]).decode('ascii')
        with self.assertRaisesRegex(ValueError, 'replay index'):
            BatchSummary.from_dict(record)

    def test_trailing_second_owned_stream_or_bytes_are_rejected(self):
        record = summary_record()
        blob = base64.b85decode(record['replay_blob'])
        for suffix in (b'owned-tail', zlib.compress(b'owned-second-record')):
            altered = copy.deepcopy(record)
            altered['replay_blob'] = base64.b85encode(blob + suffix).decode('ascii')
            with self.assertRaisesRegex(ValueError, 'replay index'):
                BatchSummary.from_dict(altered)


if __name__ == '__main__':
    unittest.main()
