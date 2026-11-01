"""agent_forge/stamps.py: capping GitHub's future-stamped reviews and
comments by id order (#183)."""

import unittest

from agent_forge import stamps


def rev(ident, created, submitted=None, state="APPROVED"):
    return {"databaseId": ident, "state": state, "createdAt": created,
            "submittedAt": created if submitted is None else submitted}


class CorrectSequence(unittest.TestCase):

    def test_the_case_that_prompted_it(self):
        # Two reviews posted around 08:59Z came back stamped about an hour
        # later, after three approvals with higher ids.
        reviews = [rev(5727295171, "2026-11-01T09:58:54Z",
                       state="CHANGES_REQUESTED"),
                   rev(5727295698, "2026-11-01T09:59:06Z",
                       state="COMMENTED"),
                   rev(5727318703, "2026-11-01T09:07:28Z"),
                   rev(5727329259, "2026-11-01T09:16:00Z"),
                   rev(5727333578, "2026-11-01T09:17:30Z")]
        self.assertEqual(stamps.correct_sequence(reviews), 2)
        self.assertEqual([r["createdAt"] for r in reviews[:2]],
                         ["2026-11-01T09:07:28Z"] * 2)
        self.assertEqual([r["submittedAt"] for r in reviews[:2]],
                         ["2026-11-01T09:07:28Z"] * 2)
        newest = max(reviews, key=lambda r: r["submittedAt"])
        self.assertEqual(newest["databaseId"], 5727333578)

    def test_right_stamps_are_left_alone(self):
        reviews = [rev(1, "2026-11-01T08:00:00Z"),
                   rev(2, "2026-11-01T08:05:00Z")]
        before = [dict(r) for r in reviews]
        self.assertEqual(stamps.correct_sequence(reviews), 0)
        self.assertEqual(reviews, before)

    def test_the_newest_is_not_capped_at_the_read_time(self):
        # A cap that moved every tick would change the Merger's comment
        # keys every tick, and it would post the same comment each time.
        comments = [{"databaseId": 9, "createdAt": "2099-01-01T00:00:00Z"}]
        self.assertEqual(stamps.correct_sequence(comments), 0)
        self.assertEqual(comments[0]["createdAt"], "2099-01-01T00:00:00Z")

    def test_a_pending_reviews_late_submission_stands(self):
        # Started first (lower id) but submitted after a newer review: its
        # createdAt is in order, so its submittedAt is right and stays.
        reviews = [rev(1, "2026-11-01T08:00:00Z", "2026-11-01T08:40:00Z"),
                   rev(2, "2026-11-01T08:10:00Z")]
        self.assertEqual(stamps.correct_sequence(reviews), 0)
        self.assertEqual(reviews[0]["submittedAt"], "2026-11-01T08:40:00Z")

    def test_submission_moves_back_by_the_same_amount(self):
        reviews = [rev(1, "2026-11-01T09:30:00Z", "2026-11-01T09:45:00Z"),
                   rev(2, "2026-11-01T09:00:00Z")]
        stamps.correct_sequence(reviews)
        self.assertEqual(reviews[0]["createdAt"], "2026-11-01T09:00:00Z")
        self.assertEqual(reviews[0]["submittedAt"], "2026-11-01T09:15:00Z")

    def test_submission_never_goes_below_creation(self):
        reviews = [rev(1, "2026-11-01T09:30:00Z", "2026-11-01T09:31:00Z"),
                   rev(2, "2026-11-01T09:00:00Z")]
        reviews[0]["submittedAt"] = "2026-11-01T09:10:00Z"
        stamps.correct_sequence(reviews)
        self.assertEqual(reviews[0]["submittedAt"], "2026-11-01T09:00:00Z")

    def test_records_without_an_id_or_creation_are_untouched(self):
        records = [{"createdAt": "2026-11-01T11:00:00Z"},
                   {"databaseId": 3, "submittedAt": "2026-11-01T11:00:00Z"},
                   {"databaseId": 4, "createdAt": None}]
        before = [dict(r) for r in records]
        self.assertEqual(stamps.correct_sequence(records), 0)
        self.assertEqual(records, before)

    def test_correcting_twice_changes_nothing_more(self):
        reviews = [rev(1, "2026-11-01T09:58:54Z"),
                   rev(2, "2026-11-01T09:07:28Z")]
        stamps.correct_sequence(reviews)
        once = [dict(r) for r in reviews]
        self.assertEqual(stamps.correct_sequence(reviews), 0)
        self.assertEqual(reviews, once)


    def test_records_sharing_an_id_dont_cap_each_other(self):
        comments = [{"databaseId": 7, "createdAt": "2026-11-01T09:00:00Z"},
                    {"databaseId": 7, "createdAt": "2026-11-01T09:10:00Z"},
                    {"databaseId": 3, "createdAt": "2026-11-01T09:20:00Z"}]
        stamps.correct_sequence(comments)
        self.assertEqual([c["createdAt"] for c in comments],
                         ["2026-11-01T09:00:00Z", "2026-11-01T09:10:00Z",
                          "2026-11-01T09:00:00Z"])


class Correct(unittest.TestCase):

    def test_reviews_and_comments_are_separate_sequences(self):
        # A comment id far above every review id caps no review.
        node = {"reviews": [rev(10, "2026-11-01T09:20:00Z")],
                "comments": [{"databaseId": 99999,
                              "createdAt": "2026-11-01T09:00:00Z"}]}
        stamps.correct(node)
        self.assertEqual(node["reviews"][0]["createdAt"],
                         "2026-11-01T09:20:00Z")

    def test_connections_are_corrected_too(self):
        # Rules that keep reviews as connections, not flattened lists.
        node = {"reviews": {"nodes": [rev(1, "2026-11-01T09:58:54Z"),
                                      rev(2, "2026-11-01T09:07:28Z")]},
                "comments": None}
        self.assertIs(stamps.correct(node), node)
        self.assertEqual(node["reviews"]["nodes"][0]["createdAt"],
                         "2026-11-01T09:07:28Z")


if __name__ == "__main__":
    unittest.main()
