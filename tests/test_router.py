"""Tests for agent_forge.router: the Decision, and finding the rules."""
import dataclasses
import os
import tempfile
import unittest

from agent_forge import router, rules_default

RULES_FILE = '''
graphql = {"Issue": "title"}
class Roster:
    def __init__(self, project):
        self.project = project
def configure(project, merger):
    pass
def decide(item, ctx):
    return []
'''


class Decisions(unittest.TestCase):

    def test_frozen_with_the_defaults(self):
        d = router.Decision(3, "LeadDev", 8, "2026-10-09T11:00:00Z", "#3 x")
        self.assertEqual((d.kind, d.suppressed, d.actions, d.reminder),
                         ("turn", None, (), None))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            d.agent = "Reviewer"

    def test_the_context_finds_items_by_number(self):
        ctx = router.Context(None, None, [{"number": 4}], [{"number": 2}])
        self.assertEqual(sorted(ctx.items), [2, 4])
        self.assertIsNone(ctx.only)

    def test_the_kinds(self):
        self.assertEqual(router.KINDS, ("turn", "notice", "relook",
                                        "surface"))


class Reminders(unittest.TestCase):

    def test_doubling_to_the_ceiling(self):
        policy = router.Reminder(600, 2400)
        self.assertEqual([policy.interval(n) for n in range(5)],
                         [600, 1200, 2400, 2400, 2400])
        self.assertEqual(policy.interval(10 ** 6), 2400)

    def test_jitter_delays_by_at_most_its_share_and_never_less(self):
        policy = router.Reminder(600, 2400, jitter=0.5)
        waits = [policy.wait("key %d" % k, 0) for k in range(200)]
        self.assertTrue(all(600 <= w < 900 for w in waits))
        self.assertGreater(len(set(waits)), 100)
        self.assertEqual(router.Reminder(600, 2400, jitter=0).wait("k", 1),
                         1200)

    def test_jitter_is_fixed_by_the_key_and_the_count(self):
        policy = router.Reminder(600, 2400)
        self.assertEqual(policy.wait("a", 0), policy.wait("a", 0))
        self.assertNotEqual(policy.wait("a", 2), policy.wait("b", 2))
        self.assertNotEqual(policy.wait("a", 2) - 2400,
                            policy.wait("a", 3) - 2400)

    def test_a_policy_that_makes_no_sense_is_refused(self):
        for args in ((0, 10), (10, 5), (10, 20, -1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                router.Reminder(*args)


class Actions(unittest.TestCase):

    class GitHub:
        def __init__(self, answer):
            self.answer, self.calls = answer, []

        def write(self, method, path, **fields):
            self.calls.append((method, path, fields))
            return self.answer

    def test_add_label_posts_the_label(self):
        github = self.GitHub((True, ""))
        action = router.AddLabel(41, "Reviewer:Bea")
        self.assertEqual(action.run(github, "example/widget"), (True, ""))
        self.assertEqual(github.calls, [(
            "POST", "repos/example/widget/issues/41/labels",
            {"labels[]": "Reviewer:Bea"})])
        self.assertEqual(action.describe(), "add label Reviewer:Bea to #41")
        self.assertIsInstance(action, router.Action)

    def test_a_refusal_is_returned_not_raised(self):
        self.assertEqual(router.AddLabel(4, "x").run(
            self.GitHub((False, "HTTP 403")), "example/widget"),
            (False, "HTTP 403"))


class LoadRules(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w") as handle:
            handle.write(text)

    def test_default_is_the_shipped_rules(self):
        self.assertIs(router.load_rules("default"), rules_default)

    def test_a_file_in_the_project_gets_the_optional_parts(self):
        self.write("rules.py", RULES_FILE)
        rules = router.load_rules("rules.py", self.root)
        self.assertEqual((rules.flatten, rules.lookups, rules.sweep(None),
                          rules.summary(None)), ((), (), [], ""))
        self.assertIs(rules.recheck, rules.decide)

    def test_what_isnt_a_rules_module_is_refused(self):
        self.write("half.py", "graphql = {}\n")
        self.write("broken.py", "raise ValueError('no')\n")
        for name, root, says in [
                ("", None, "must name a rules module"),
                ("rules.py", None, "checkout is unknown"),
                ("/abs/rules.py", self.root, "relative to the project"),
                ("missing.py", self.root, "no such file"),
                ("half.py", self.root, "has no Roster"),
                ("broken.py", self.root, "no")]:
            with self.subTest(name=name), \
                    self.assertRaises(router.RulesError) as caught:
                router.load_rules(name, root)
            self.assertIn(says, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
