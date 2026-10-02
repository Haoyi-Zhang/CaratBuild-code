from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import unittest
from carat.public_pair import extract_pair, payload
from carat.decode import decode
from carat.independent_check import check

ROOT = Path(__file__).resolve().parents[1]

def corpus():
    return json.loads((ROOT/"external_inputs/pytest_pair.json").read_text())

class PublicPairTests(unittest.TestCase):
    def test_exact_payload_and_mass(self):
        data = corpus()
        self.assertEqual([len(payload(item["hunk"])) for item in data["source"]], [5,6,4])
        facts = extract_pair(data)
        self.assertEqual(len(facts), 6)
        self.assertEqual(decode(facts).gross_mass, 15)
        self.assertTrue(check(facts).accepted)

    def test_only_coordinates_may_shift(self):
        data = corpus()
        data["target"][1]["hunk"] = data["target"][1]["hunk"].replace("-814,6 +814,12", "-7,6 +13,12")
        self.assertTrue(check(extract_pair(data)).accepted)
        data["target"][1]["hunk"] = data["target"][1]["hunk"].replace('name.endswith("conftest.py")', 'name.endswith("another.py")')
        with self.assertRaises(ValueError): extract_pair(data)

    def test_synchronized_payload_changes_remain_a_declared_pair(self):
        data = corpus()
        for side in ("source", "target"):
            data[side][0]["hunk"] = data[side][0]["hunk"].replace(
                "Previously this resulted", "Before this change it resulted"
            )
        facts = extract_pair(data)
        self.assertTrue(check(facts).accepted)
        self.assertEqual(decode(facts).gross_mass, 15)

    def test_context_coordinates_and_valid_metadata_are_not_payload_identity(self):
        data = corpus()
        data["target"][1]["hunk"] = data["target"][1]["hunk"].replace(
            "@@ -814,6 +814,12 @@ def consider_pluginarg(self, arg: str) -> None:",
            "@@ -7,6 +13,12 @@ another context label",
        ).replace(
            "             if name in essential_plugins:",
            "             # different unchanged context",
        )
        data["repository"] = "example/declared-repository"
        data["source_record"] = "https://example.test/source"
        data["target_record"] = "https://example.test/target"
        self.assertTrue(check(extract_pair(data)).accepted)

    def test_metadata_shape_is_bounded_and_explicit(self):
        for mutate in (
            lambda data: data.update(repository=""),
            lambda data: data.update(access_date="not-a-date"),
            lambda data: data.update(source_record="file:///tmp/source"),
            lambda data: data.update(extra="unsupported"),
        ):
            data = corpus()
            mutate(data)
            with self.assertRaises(ValueError):
                extract_pair(data)

    def test_path_truncation_and_binary_fail_closed(self):
        original = corpus()
        for field, value in [("path", "different.py"), ("hunk", "Binary files differ\n"),
                             ("hunk", original["target"][2]["hunk"].splitlines(keepends=True)[0])]:
            data = deepcopy(original)
            data["target"][2][field] = value
            with self.assertRaises(ValueError): extract_pair(data)
