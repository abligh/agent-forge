"""Tests for agent_forge/nightly.py: the fixes of a red nightly, as
forge-nightly's state tells the other tools."""
import datetime
import json
import os
import tempfile
import unittest

from agent_forge import config, nightly
from tests.helpers import example

NOW = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.timezone.utc)


class Fixes(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {"FORGE_STATE_DIR": self.dir.name, "HOME": self.dir.name}
        data = example()
        data["router"] = {"owners": {"triage": "LeadDev"}}
        data["nightly"] = {"workflow": "ci.yaml", "job": "build"}
        self.project = config.Project(data)

    def write(self, **saved):
        path = nightly.path("widget", self.env, create=True)
        with open(path, "w") as handle:
            json.dump(saved, handle)

    def fixes(self, now=NOW):
        return nightly.fixes(self.project, self.env, now)

    def test_the_open_issues_fixes(self):
        self.write(at="2026-10-23T11:55:00Z", issue=7, issue_open=True,
                   fixes=[12, 14, True, "15"])
        self.assertEqual(self.fixes(), frozenset({12, 14}))

    def test_none_once_the_issue_is_closed(self):
        self.write(at="2026-10-23T11:55:00Z", issue=7, issue_open=False,
                   fixes=[12])
        self.assertEqual(self.fixes(), frozenset())

    def test_a_stale_file_is_not_read(self):
        self.write(at="2026-10-23T11:00:00Z", issue=7, issue_open=True,
                   fixes=[12])
        self.assertEqual(self.fixes(), frozenset())
        self.assertEqual(self.fixes(NOW - datetime.timedelta(minutes=45)),
                         frozenset({12}))

    def test_none_without_the_file_or_the_section(self):
        self.assertEqual(self.fixes(), frozenset())
        # Reading creates no state directory of forge-nightly's.
        self.assertFalse(os.path.exists(os.path.join(self.dir.name,
                                                     "widget")))
        self.write(at="2026-10-23T11:55:00Z", issue_open=True, fixes=[12])
        del self.project.data["nightly"]
        self.assertEqual(self.fixes(), frozenset())

    def test_a_broken_file_is_none(self):
        path = nightly.path("widget", self.env, create=True)
        for text in ("{", "[]", '{"at": 3, "issue_open": true}',
                     '{"at": "2026-10-23T11:55:00Z", "issue_open": true, '
                     '"fixes": 12}'):
            with self.subTest(text=text):
                with open(path, "w") as handle:
                    handle.write(text)
                self.assertEqual(self.fixes(), frozenset())


if __name__ == "__main__":
    unittest.main()
