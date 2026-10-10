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
