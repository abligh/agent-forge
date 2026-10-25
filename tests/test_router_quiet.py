"""Tests for the default rules' quiet labels (`router.quiet_labels`):
an open item with one is nobody's under every rule, with no reminder,
no offer and no notice, and the rules decide as before without them.
"""

import os
import tempfile
import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, NOW, REVIEWER, T2,
                                     FakeGitHub, comment, issue_node, pr_node,
                                     r, response, status)
from tests.test_router_idle_offer import directory, entry, owned

QUIET = ["Status:waiting-backlog", "Status:venue"]


def project(quiet=QUIET, **more):
    data = example()
    data["router"] = dict(more, **({"quiet_labels": quiet}
                                   if quiet is not None else {}))
    return config.Project(data, None)


def configure(test, quiet=QUIET, **more):
    r.configure(project(quiet, **more))
    test.addCleanup(r.configure, config.Project(example(), None))


def decide(*items, roster=None, **ctx):
    prs, issues = r.fetch(FakeGitHub(response(
        [i for i in items if "headRefOid" in i],
        [i for i in items if "headRefOid" not in i])))
    context = router.Context(roster or rules.Roster(project()), NOW, prs,
                             issues, **ctx)
    return [d for item in prs + issues for d in rules.decide(item, context)]


class Config(unittest.TestCase):

    def test_off_without_the_key(self):
        configure(self, None)
        self.assertEqual(rules.QUIET_LABELS, ())

    def test_a_module_reads_it_from_its_project(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "rules.py"), "w") as handle:
                handle.write("graphql = {}\nRoster = None\nseen = []\n"
                             "def configure(p, m):\n"
                             "    seen.append(p.section('router')"
                             "['quiet_labels'])\n"
                             "def decide(i, c): return []\n")
            data = example()
            data["router"] = {"rules": "rules.py", "quiet_labels": QUIET}
            r.configure(config.Project(data, os.path.join(
                root, config.CONFIG_NAME)))
            self.addCleanup(r.configure, config.Project(example(), None))
            seen = r.RULES.seen
        self.assertEqual(seen, [QUIET])


class Quiet(unittest.TestCase):

    def setUp(self):
        configure(self)

    def test_a_pull_request_that_would_be_a_turn(self):
        # Red at the head: rule 4, the author's, without the label.
        pr = pr_node(41, labels=["Status:venue"],
                     statuses=(status("FAILURE"),))
        (d,) = decide(pr)
        self.assertEqual((d.agent, d.rule, d.suppressed),
                         (None, None, "quiet: Status:venue"))
        configure(self, None)
        self.assertEqual(decide(pr)[0].rule, 4)

    def test_an_issue_and_the_first_label_of_the_list(self):
        issue = issue_node(22, labels=["Owner:LeadDev", "Status:venue",
                                       "Status:waiting-backlog"],
                           comments=[comment(REVIEWER, T2)])
        (d,) = decide(issue)
        self.assertEqual(d.suppressed, "quiet: Status:waiting-backlog")
        configure(self, ["Other"])
        self.assertEqual(decide(issue)[0].rule, 12)

    def test_before_the_stuck_rules_and_rule_18(self):
        configure(self, owners={"triage": "LeadDev"},
                  stuck={"draft_hours": 1})
        draft = pr_node(41, draft=True, labels=["Status:venue"])
        unowned = issue_node(22, author="someone", labels=["Status:venue"])
        self.assertEqual([d.suppressed for d in decide(draft, unowned)],
                         ["quiet: Status:venue"] * 2)

    def test_not_offered_to_an_idle_owner(self):
        configure(self, idle_offer={"report_to": "LeadDev"})
        prs, issues = r.fetch(FakeGitHub(response([], [
            owned(30, owner="UIDev", labels=["Status:venue"])])))
        got = rules.sweep(router.Context(
            rules.Roster(project()), NOW, prs, issues,
            directory=directory(entry("WidgetUIDev"))))
        self.assertEqual([(d.agent, d.rule) for d in got],
                         [("LeadDev", 16)])
        self.assertIn("1 issue waiting", got[0].line)

    def test_no_reminder_through_the_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            posts = []
            issue = issue_node(22, labels=["Owner:LeadDev"],
                               comments=[comment(REVIEWER, T2)])
            for labels in ([], ["Status:venue"]):
                node = dict(issue, labels={"nodes": [
                    {"name": n} for n in ["Owner:LeadDev"] + labels]})
                r.run(FakeGitHub(response([], [node])), r.RULES.Roster(
                    project()), tmp, True, lambda s, t: posts.append(s),
                    None, lambda line: None, when=NOW, remind_s=1)
            self.assertEqual(posts, ["WidgetLeadDev"])

    def test_no_notice_of_a_path_no_reviewer_covers(self):
        configure(self, owners={"triage": "LeadDev"}, unreviewed={})
        for labels, rules_19 in ((["Status:venue"], []), ([], [19])):
            pr = pr_node(41, author=LEADDEV, labels=labels,
                         files=(("odd/x.py", 3),))
            prs, issues = r.fetch(FakeGitHub(response([pr], [])))
            with tempfile.TemporaryDirectory() as tmp:
                got = rules.sweep(router.Context(
                    rules.Roster(project()), NOW, prs, issues,
                    state_dir=tmp))
            self.assertEqual([d.rule for d in got], rules_19)


if __name__ == "__main__":
    unittest.main()
