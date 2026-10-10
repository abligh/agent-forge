"""Tests for agent_forge.state."""
import os
import tempfile
import unittest
from unittest import mock

from agent_forge import state


class State(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")

    def test_round_trip_with_defaults(self):
        state.save(self.path, {"a": 1})
        self.assertEqual(state.load(self.path, {"a": 0, "queue": {}}),
                         {"a": 1, "queue": {}})

    def test_defaults_are_copies(self):
        defaults = {"queue": {}}
        state.load(self.path, defaults)["queue"]["x"] = 1
        self.assertEqual(defaults, {"queue": {}})

    def test_missing_corrupt_or_not_an_object_starts_afresh(self):
        self.assertEqual(state.load(self.path), {})
        for text in ("{half", "[1, 2]"):
            with open(self.path, "w") as handle:
                handle.write(text)
            self.assertEqual(state.load(self.path), {})

    def test_a_failed_write_leaves_the_old_file_and_no_litter(self):
        state.save(self.path, {"old": True})
        with mock.patch("json.dump", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                state.save(self.path, {"new": True})
        self.assertEqual(state.load(self.path), {"old": True})
        self.assertEqual(os.listdir(self.tmp.name), ["state.json"])


if __name__ == "__main__":
    unittest.main()
