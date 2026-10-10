"""Tests for agent_forge.signing."""
import unittest

from agent_forge import signing

S = "— Reviewer"


class Agent(unittest.TestCase):

    def test_never_guessed(self):
        with self.assertRaises(signing.SigningError) as caught:
            signing.agent({})
        self.assertEqual(caught.exception.status, 2)

    def test_a_bare_name_only(self):
        self.assertEqual(signing.agent({"FORGE_AGENT": "Reviewer"}),
                         "Reviewer")
        for bad in ("../x", "a b", "a/b"):
            with self.assertRaises(signing.SigningError):
                signing.agent({"FORGE_AGENT": bad})


class Signed(unittest.TestCase):

    def test_appends_one(self):
        self.assertEqual(signing.signed("Hello.\n", "Reviewer"),
                         "Hello.\n\n%s\n" % S)

    def test_a_caller_who_also_signs_gets_one(self):
        self.assertEqual(signing.signed("Hello.\n\n%s\n\n%s\n\n" % (S, S),
                                        "Reviewer"),
                         "Hello.\n\n%s\n" % S)

    def test_a_quoted_sign_off_mid_body_is_kept_and_the_end_signed(self):
        body = "As\n%s\nsaid, yes.\n" % S
        self.assertEqual(signing.signed(body, "Reviewer"),
                         body + "\n%s\n" % S)

    def test_only_its_own_sign_off_is_stripped(self):
        self.assertEqual(signing.signed("x\n\n— LeadDev\n", "Reviewer"),
                         "x\n\n— LeadDev\n\n%s\n" % S)

    def test_a_body_that_is_only_a_sign_off(self):
        self.assertEqual(signing.signed(S, "Reviewer"), "\n\n%s\n" % S)

    def test_is_signed_wants_the_whole_line(self):
        self.assertTrue(signing.is_signed("x\n%s\n" % S, "Reviewer"))
        self.assertFalse(signing.is_signed("x %s\n" % S, "Reviewer"))


class Mentions(unittest.TestCase):

    def test_found_at_a_word_boundary(self):
        self.assertEqual(signing.mentions("hi @Reviewer, see\n(@LeadDev)"),
                         [(1, "@Reviewer"), (2, "@LeadDev")])

    def test_versions_and_paths_are_not_mentions(self):
        self.assertEqual(signing.mentions(
            "serde@1.0.210 owner/repo@abc123 a-b@c me@example.org"), [])

    def test_code_is_inert(self):
        self.assertEqual(signing.mentions(
            "say `@Reviewer`\n```\n@LeadDev\n```\n"), [])

    def test_an_unterminated_fence_hides_nothing(self):
        self.assertEqual(signing.mentions("```\n@LeadDev\n"),
                         [(2, "@LeadDev")])

    def test_backticked_handles_are_listed(self):
        self.assertEqual(signing.code_handles("`@B` and `@A` and `@B`"),
                         ["`@A`", "`@B`"])


if __name__ == "__main__":
    unittest.main()
