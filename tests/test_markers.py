"""Tests for agent_forge.markers."""
import datetime
import unittest

from agent_forge import markers

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class Prose(unittest.TestCase):

    def test_code_is_not_prose(self):
        text = ("Blocked-by: #1\n```\nBlocked-by: #2\n```\n"
                "    Blocked-by: #3\n~~~md\nBlocked-by: #4")
        self.assertEqual(markers.blockers(text), {1})


class NotAReview(unittest.TestCase):

    def test_opening_a_line_in_any_case(self):
        self.assertTrue(markers.is_not_a_review("Not-a-review\n\nplease look"))
        self.assertTrue(markers.is_not_a_review("x\nnot-a-review: ask"))
        self.assertFalse(markers.is_not_a_review("this is Not-a-review"))
        self.assertFalse(markers.is_not_a_review(None))


class Blockers(unittest.TestCase):

    def test_one_per_line_bold_or_not(self):
        self.assertEqual(markers.blockers(
            "Blocked-by: #12\n**Blocked-by:** #13\n**Blocked-by: #14**\n"
            "blocked-by:#15\nBlocked-by: #16 and #17"), {12, 13, 14, 15})


class Defer(unittest.TestCase):

    def test_formats(self):
        for value, when in (
                ("2026-10-11T09:30:00Z", datetime.datetime(2026, 10, 11, 9, 30,
                                                           tzinfo=UTC)),
                ("2026-10-11T09:30+0100", datetime.datetime(
                    2026, 10, 11, 8, 30, tzinfo=UTC)),
                ("2026-10-11", datetime.datetime(2026, 10, 11, tzinfo=UTC))):
            self.assertEqual(markers.parse_time(value), when)
        self.assertIsNone(markers.parse_time("tomorrow"))

    def test_the_newest_line_wins(self):
        texts = [("2026-10-10T10:00:00Z", "Defer-until: 2026-10-10T11:00:00Z"),
                 ("", "Defer-until: 2026-12-01"),
                 ("2026-10-10T09:00:00Z", "Defer-until: 2026-10-12")]
        self.assertIsNone(markers.deferred(texts, NOW))

    def test_unexpired_holds(self):
        self.assertEqual(markers.deferred([("", "Defer-until: 2026-10-11")],
                                          NOW),
                         "deferred until 2026-10-11T00:00:00Z")

    def test_unreadable_holds(self):
        self.assertEqual(markers.deferred([("", "Defer-until: soon")], NOW),
                         "Defer-until: soon is not a time")

    def test_none_is_no_deferral(self):
        self.assertIsNone(markers.deferred([("", "nothing")], NOW))


if __name__ == "__main__":
    unittest.main()
