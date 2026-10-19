"""Tests for agent_forge/quiet.py: quiet holds on a shared host."""

import datetime
import os
import tempfile
import unittest

from agent_forge import quiet

T0 = datetime.datetime(2026, 10, 19, 15, 0, tzinfo=datetime.timezone.utc)


def minutes(n):
    return datetime.timedelta(minutes=n)


class Holds(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w") as handle:
            handle.write(text)

    def test_a_hold_is_live_until_its_time_and_says_why(self):
        until = quiet.hold(self.dir, "Alice", 30, "timing run", T0)
        self.assertEqual(until, T0 + minutes(30))
        self.assertEqual(quiet.live(self.dir, T0 + minutes(29)),
                         [("Alice", until, "timing run")])
        self.assertEqual(quiet.live(self.dir, T0 + minutes(30)), [])

    def test_holding_again_replaces_the_hold(self):
        quiet.hold(self.dir, "A", 30, "first", T0)
        quiet.hold(self.dir, "A", 5, "second", T0)
        self.assertEqual(quiet.live(self.dir, T0),
                         [("A", T0 + minutes(5), "second")])

    def test_holds_come_soonest_to_end_first(self):
        quiet.hold(self.dir, "Late", 60, "x", T0)
        quiet.hold(self.dir, "Soon", 10, "y", T0)
        self.assertEqual([h[0] for h in quiet.live(self.dir, T0)],
                         ["Soon", "Late"])

    def test_a_file_that_cannot_be_read_holds_nothing(self):
        self.write("Garbled", "until tomorrow-ish\n")
        self.write("Empty", "")
        self.write("NoTime", "hold everything\n")
        self.write(".hidden", "until 2099-01-01T00:00Z x\n")
        self.assertEqual(quiet.live(self.dir, T0), [])

    def test_a_file_written_by_hand_counts(self):
        self.write("Bob", "until 2026-10-19T16:30+01:00 A/B\nmore\n")
        self.assertEqual(quiet.live(self.dir, T0), [(
            "Bob", datetime.datetime(
                2026, 10, 19, 15, 30, tzinfo=datetime.timezone.utc), "A/B")])

    def test_no_directory_holds_nothing(self):
        self.assertEqual(quiet.live(os.path.join(self.dir, "none"), T0), [])

    def test_limits_on_a_hold(self):
        for bad in (0, -1, 121):
            with self.assertRaises(ValueError):
                quiet.hold(self.dir, "A", bad, "x", T0)
        quiet.hold(self.dir, "A", 200, "x", T0, max_minutes=240)
        with self.assertRaises(ValueError):
            quiet.hold(self.dir, "../etc", 5, "x", T0)
        with self.assertRaises(ValueError):
            quiet.hold(self.dir, "A", 5, "two\nlines", T0)

    def test_release(self):
        quiet.hold(self.dir, "A", 5, "x", T0)
        self.assertTrue(quiet.release(self.dir, "A"))
        self.assertFalse(quiet.release(self.dir, "A"))
        self.assertEqual(quiet.live(self.dir, T0), [])


if __name__ == "__main__":
    unittest.main()
