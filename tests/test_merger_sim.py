"""The Merger over a simulated morning (tests/merger_sim.py,
docs/merger-sim.md): the real tick, minute after minute, against real
git, a GitHub that behaves as GitHub does where it matters, and a model
of the build check.

The unit tests in test_forge_merger.py test each function against the
assumptions of its own design. These test what happens across ticks,
where GitHub's own update commits, restacks, retargets and a gated
instance's late verdicts meet: the faults of 2026-11-11 (a train spent
by the Merger's own update of its member, stack layers boarding before
their gated instance) lived there and nowhere else.

AGENT_FORGE_SIM_LONG=1 adds a longer, busier run."""

import os
import tempfile
import unittest

from tests import merger_sim as ms


def simulate(merger, scenario, minutes=300, **check):
    with tempfile.TemporaryDirectory() as root:
        return ms.Sim(merger, root, scenario, **check).run(minutes)


def figures(result):
    return ("%d merged in %.2f h (%.1f an hour), %d build runs (%.2f a "
            "merge, %d of them batch tops)" % (
                result.merged, result.hours, result.merged / result.hours,
                result.runs, result.runs / max(result.merged, 1),
                result.top_runs))


class Morning(unittest.TestCase):

    def test_the_merger_keeps_every_rule(self):
        m = ms.load_merger()
        m.configure(ms.project())
        got = simulate(m, ms.day())
        self.assertEqual(got.broken, [], figures(got))
        # Every pull request merges but the red one, which is the only
        # one handed back.
        self.assertEqual(got.merged, 15, figures(got))
        self.assertLessEqual(got.runs / got.merged, 3.0, figures(got))

    def test_a_restacked_layer_keeps_its_train(self):
        # 2026-11-12: the Merger restacked a train's layer, and the next
        # tick held it for its gated instance's verdict on the new head
        # (the walk back to an earlier verdict stopped at the restack),
        # so it left its train as a push and every train was cut again.
        m = ms.load_merger()
        m.configure(ms.project())
        got = simulate(m, ms.restacked_day())
        self.assertTrue(any("RESTACK (done)" in l for l in got.lines))
        self.assertEqual(got.broken, [], figures(got))
        self.assertFalse(any("TRAIN-LEFT" in l for l in got.lines),
                         figures(got))
        self.assertEqual(got.merged, 15, figures(got))

    def test_it_finds_the_faults_of_v0_4_173(self):
        # v0.4.173 is the last release without agent-forge #306 and #307.
        # Its trains were spent by its own updates (BATCH-FIRST, then
        # TRAIN-LEFT), and a stack's layer boarded before its gated
        # instance had passed.
        source = ms.merger_source("v0.4.173")
        if source is None:
            self.skipTest("no v0.4.173 in this checkout")
        m = ms.load_merger(source)
        m.configure(ms.project())
        got = simulate(m, ms.day())
        rules = {line.split(" rule ")[1][0] for line in got.broken}
        self.assertEqual(rules, {"1", "3"}, "\n".join(got.broken))
        self.assertTrue(any("BATCH-FIRST" in l for l in got.lines))
        self.assertTrue(any("TRAIN-LEFT" in l for l in got.lines))

    @unittest.skipUnless(os.environ.get("AGENT_FORGE_SIM_LONG"),
                         "AGENT_FORGE_SIM_LONG=1 runs it")
    def test_a_busy_day_keeps_every_rule(self):
        m = ms.load_merger()
        m.configure(ms.project(batch=6, batches=3))
        got = simulate(m, ms.busy(), minutes=900, slots=6)
        self.assertEqual(got.broken, [], figures(got))
        print("\nbusy day: " + figures(got))


class Rules(unittest.TestCase):
    """The monitor itself: a break it should see, it sees."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sim = ms.Sim(None, tmp.name, [])
        self.world = self.sim.world

    def merge(self, number):
        pr = self.world.prs[number]
        ok, why = self.sim.github.write(
            "PUT", "repos/%s/pulls/%d/merge" % (ms.SLUG, number),
            sha=pr["head"], merge_method="squash")
        self.assertTrue(ok, why)
        self.sim.monitor.check()

    def test_a_tree_nothing_tested_is_a_break(self):
        self.world.open(1)
        self.merge(1)
        self.assertEqual([l.split(" ", 1)[1] for l in self.sim.monitor.broken],
                         ["rule 2: #1 merged a tree no green run covered"])

    def test_a_red_change_on_the_default_branch_is_a_break(self):
        self.world.open(1, red=True)
        self.merge(1)
        self.assertIn("rule 4: #1 merged a red tree",
                      [l.split(" ", 1)[1] for l in self.sim.monitor.broken])

    def test_a_tested_tree_is_not(self):
        self.world.open(1)
        pr = self.world.prs[1]
        tree = self.world.merge_tree(self.world.main(), pr["head"])
        self.sim.check.state["results"]["x" * 40] = {"tree": tree,
                                                     "ok": True}
        self.merge(1)
        self.assertEqual(self.sim.monitor.broken, [])

    def test_boarding_without_the_gated_green_is_a_break(self):
        self.world.open(1, mac=True)
        pr = self.world.prs[1]
        with open(os.path.join(self.sim.state, "ahead.json"), "w") as out:
            out.write('{"entries": [{"pr": 1, "sha": "%s", "tree": "t1"},'
                      ' {"pr": 2, "sha": "%s", "tree": "t2"}],'
                      ' "batch": {"size": 2, "trees": ["t1", "t2"]}}'
                      % (pr["head"], "b" * 40))
        self.world.open(2)
        self.sim.monitor.check()
        self.assertEqual(
            [l.split(" ", 1)[1] for l in self.sim.monitor.broken],
            ["rule 3: #1 boarded with vm/mac not yet green on its change"])
