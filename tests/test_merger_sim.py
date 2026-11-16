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

import datetime
import json
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
        # 3.13 since 2026-11-13: the urgent one rides the next train
        # rather than cutting them again (it merges at the same minute).
        self.assertLessEqual(got.runs / got.merged, 3.2, figures(got))

    def test_a_restacked_layer_keeps_its_train(self):
        # 2026-11-12: the Merger restacked a train's layer, and the next
        # tick held it for its gated instance's verdict on the new head
        # (the walk back to an earlier verdict stopped at the restack),
        # so it left its train as a push and every train was cut again.
        m = ms.load_merger()
        m.configure(ms.project())
        got = simulate(m, ms.restacked_day())
        # Brought up to date by the Merger: at its base's merge
        # (RESTACK), or before it, while the base was open (STALE,
        # designs/0025, fourth amendment of 2026-11-11).
        self.assertTrue(any("RESTACK (done)" in l or (
            l[6:].startswith("STALE #21") and "merged #20's head" in l)
            for l in got.lines), "\n".join(got.lines))
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

    def test_a_red_train_is_searched_and_kept(self):
        # 2026-11-12, with train_merge: the tick that found a train's
        # top red updated its first member, a suspect; the search lost
        # it and its stacked layers, forgot that the whole train was
        # red, and every train was cut again with newer pull requests
        # in the middle.
        m = ms.load_merger()
        m.configure(ms.project(batch=8, train_merge=True,
                               layers_need_own_green=False))
        got = simulate(m, ms.red_stack_day())
        self.assertEqual(got.broken, [], figures(got))
        red = [l for l in got.lines if " top red" in l]
        self.assertTrue(red, figures(got))
        start = red[0][:5]
        found = [l for l in got.lines if l[6:].startswith("SUSPECT-RED #32")]
        self.assertTrue(found, "\n".join(got.lines))
        during = [l for l in got.lines if start <= l[:5] <= found[0][:5]]
        self.assertFalse([l for l in during if "UPDATE" in l
                          and l[:5] < found[0][:5]], "\n".join(during))
        self.assertFalse([l for l in during if "red from none" in l],
                         "\n".join(during))
        self.assertFalse([l for l in got.lines if l[:5] >= start
                          and l[6:].startswith("TRAIN-CUT 1")],
                         "\n".join(got.lines))

    @unittest.skipUnless(os.environ.get("AGENT_FORGE_SIM_LONG"),
                         "AGENT_FORGE_SIM_LONG=1 runs it")
    def test_a_busy_day_keeps_every_rule(self):
        m = ms.load_merger()
        m.configure(ms.project(batch=6, batches=3))
        got = simulate(m, ms.busy(), minutes=900, slots=6)
        self.assertEqual(got.broken, [], figures(got))
        print("\nbusy day: " + figures(got))


class TopsByTree(unittest.TestCase):
    """SimCheck keeps a train's run across a re-cut that makes the same
    prediction under a new commit, as forge-pr-check does (its
    next_ahead and merger_spent go by tree, #391 item 2)."""

    def predict(self, world, merger_dir, top):
        base = world.main()
        first = world.og("commit-tree", world.tree(base), "-p", base,
                         "-m", "first")
        entries = [{"pr": 1, "sha": first, "tree": world.tree(first),
                    "head": first},
                   {"pr": 2, "sha": top, "tree": world.tree(top),
                    "head": top}]
        with open(os.path.join(merger_dir, "ahead.json"), "w") as handle:
            json.dump({"entries": entries, "batch": {
                "size": 2, "trees": [e["tree"] for e in entries]}}, handle)

    def recut(self, **check):
        with tempfile.TemporaryDirectory() as root:
            world = ms.World(root)
            merger_dir = os.path.join(root, "merger")
            os.makedirs(merger_dir)
            sim = ms.SimCheck(world, merger_dir, **check)
            tree = world.write_tree(world.tree(world.main()),
                                    {"two.txt": "2\n"})
            one = world.og("commit-tree", tree, "-p", world.main(),
                           "-m", "cut")
            again = world.og("commit-tree", tree, "-p", world.main(),
                             "-m", "cut again")
            self.assertNotEqual(one, again)
            self.predict(world, merger_dir, one)
            sim.step()
            self.assertEqual([r["sha"] for r in sim.running], [one])
            world.now += datetime.timedelta(minutes=1)
            self.predict(world, merger_dir, again)
            sim.step()
            return sim, one

    def test_a_recut_with_the_same_tree_keeps_the_run(self):
        sim, one = self.recut()
        self.assertEqual([r["sha"] for r in sim.running], [one])
        self.assertEqual(sim.top_runs, 1)

    def test_in_the_checks_own_order_too_and_it_is_not_spent(self):
        sim, one = self.recut(later_first=1, stop_spent=True)
        self.assertEqual([r["sha"] for r in sim.running], [one])
        self.assertEqual((sim.top_runs, sim.stopped), (1, 0))


class Trains(unittest.TestCase):
    """merge.test_ahead.train_merge, as GitHub takes it: the train's
    commits pushed to the simulated remote, its pull request opened and
    merged, the members marked merged (#391)."""

    def test_a_green_train_of_three_merges_as_one(self):
        m = ms.load_merger()
        # Boarding before their own greens, and those runs slower than
        # the train's: its green comes first, and the three go in on it.
        m.configure(ms.project({"own_head_green": False}, batch=3,
                               batches=1, train_merge=True))
        with tempfile.TemporaryDirectory() as root:
            sim = ms.Sim(m, root, [(0, "open", (n,)) for n in (1, 2, 3)],
                         own_minutes=30)
            got = sim.run(120)
            trains = sim.github.trains
        self.assertEqual(got.broken, [], figures(got))
        self.assertEqual(got.merged, 3, figures(got))
        self.assertEqual([t["members"] for t in trains.values()],
                         [[1, 2, 3]], "\n".join(got.lines))
        self.assertEqual([t["state"] for t in trains.values()], ["MERGED"])
        self.assertTrue(any("TRAIN-MERGE #1..#3: merged 3 as #8000" in l
                            for l in got.lines), "\n".join(got.lines))

    def test_the_train_gate_merges_only_trains(self):
        # designs/0055: heads are judged by the quick check, and nothing
        # merges on it alone: each pull request goes in with a train, on
        # the full check's green on the train's top. The red one is
        # found by its quick run and never merges.
        m = ms.load_merger()
        m.configure(ms.project(quick=True, batch=3, batches=1))
        scenario = [(0, "open", (n,)) for n in (1, 2, 3, 4)]
        scenario += [(1, "open", (5, None, False, True))]
        with tempfile.TemporaryDirectory() as root:
            sim = ms.Sim(m, root, scenario, quick_minutes=3)
            got = sim.run(180)
            check = sim.check
            heads = {n: p["head"] for n, p in sim.world.prs.items()}
            statuses = sim.world.statuses
            merges = sim.world.merges
        self.assertEqual(got.broken, [], figures(got))
        self.assertEqual(got.merged, 4, figures(got))
        self.assertTrue(all(m_.get("train") is not None for m_ in merges),
                        merges)
        self.assertGreaterEqual(check.quick_runs, 5)
        self.assertNotIn(5, [m_["pr"] for m_ in merges])
        # Every head's own verdict is on the quick context, none on the
        # full one.
        for n in (1, 5):
            contexts = {s["context"] for s in statuses[heads[n]]}
            self.assertIn(ms.QUICK, contexts)
            self.assertNotIn(ms.CONTEXT, contexts)

    def test_a_quick_green_covers_no_train(self):
        # A quick verdict on a top's tree neither makes the top done nor
        # lets the default branch take the tree.
        w = ms.World.__new__(ms.World)
        w.now = ms.START
        check = ms.SimCheck(w, "/nonexistent", quick_minutes=3)
        check.state["results"]["a"] = {"tree": "T", "ok": True,
                                       "tier": "quick"}
        self.assertFalse(check.green("T"))
        self.assertFalse(check.top_done({"sha": "b", "tree": "T"}))
        check.state["results"]["c"] = {"tree": "T", "ok": True}
        self.assertTrue(check.green("T"))

    def test_a_push_to_the_network_fails_the_run(self):
        origin = "/sim/origin.git"
        self.assertEqual(
            ms.local_git(["git", "-C", "w", "push", "-q", "-f", ms.REMOTE,
                          "a:refs/heads/b"], origin),
            ["git", "-C", "w", "push", "-q", "-f", origin, "a:refs/heads/b"])
        for remote in ("https://github.com/other/repo.git",
                       "ssh://git@example.com/repo.git",
                       "git@github.com:example/widget.git"):
            with self.assertRaises(ms.NetworkUsed, msg=remote):
                ms.local_git(["git", "-C", "w", "push", remote, "a:b"],
                             origin)
            with self.assertRaises(ms.NetworkUsed, msg=remote):
                ms.local_git(["git", "fetch", remote], origin)
        # Not a remote at all: left as it is.
        self.assertEqual(ms.local_git(["git", "log", "x@{1}:y"], origin),
                         ["git", "log", "x@{1}:y"])


class LeftTop(unittest.TestCase):
    """left_top_covers with stop_spent: when a member leaves a started
    train, those below it merge on the old top's green, which the check
    must not stop as spent."""

    def test_the_members_below_a_leaver_merge_on_the_old_tops_green(self):
        m = ms.load_merger()
        m.configure(ms.project({"own_head_green": False}, batch=4,
                               batches=1, train_merge=True))
        scenario = [(0, "open", (n,)) for n in (1, 2, 3, 4)] + [
            (5, "push", (4,)), (7, "review", (4,))]
        with tempfile.TemporaryDirectory() as root:
            sim = ms.Sim(m, root, scenario, own_minutes=40, later_first=1,
                         stop_spent=True)
            got = sim.run(120)
        self.assertEqual(got.broken, [], figures(got))
        self.assertTrue(any("TRAIN-UNDER-LEFT #1..#3" in l
                            for l in got.lines), "\n".join(got.lines))


class TailKeepsItsPlace(unittest.TestCase):
    """designs/0056, item 2: with live trains, the members after one
    that leaves a started train keep their place; a newcomer approved
    meanwhile goes behind them."""

    def test_the_tail_merges_before_a_newcomer(self):
        m = ms.load_merger()
        m.configure(ms.project({"own_head_green": False}, batch=5,
                               batches=1, train_merge=True))
        scenario = [(0, "open", (n,)) for n in (1, 2, 3, 4, 5)] + [
            (5, "push", (2,)), (5, "open", (6,)), (7, "review", (2,))]
        with tempfile.TemporaryDirectory() as root:
            sim = ms.Sim(m, root, scenario, own_minutes=40, later_first=1,
                         stop_spent=True)
            got = sim.run(240)
            merged = [x["pr"] for x in sim.world.merges]
            departures = sim.monitor.departures
        self.assertEqual(got.broken, [], figures(got))
        self.assertEqual(departures, {"first": 0, "middle": 1, "last": 0},
                         "\n".join(got.lines))
        self.assertTrue(any("TRAIN-LEFT #2" in l for l in got.lines),
                        "\n".join(got.lines))
        self.assertIn(6, merged, "\n".join(got.lines))
        for n in (1, 3, 4, 5):
            self.assertLess(merged.index(n), merged.index(6),
                            "\n".join(got.lines))


class Window(unittest.TestCase):
    """designs/0056, item 4: with merge.test_ahead.window, the window
    halves when a member leaves a started train, and grows back once a
    train merges whole; the rest of the train the member left merging
    isn't a whole train."""

    def test_it_halves_on_a_departure_and_grows_on_a_whole_merge(self):
        m = ms.load_merger()
        m.configure(ms.project({"own_head_green": False}, batch=4,
                               batches=1, train_merge=True,
                               window={"floor": 1}))
        # #5 comes later, so the ticks after #2's merge cut a train.
        scenario = [(0, "open", (n,)) for n in (1, 2, 3, 4)] + [
            (5, "push", (2,)), (7, "review", (2,)), (50, "open", (5,))]
        with tempfile.TemporaryDirectory() as root:
            sim = ms.Sim(m, root, scenario, own_minutes=40, later_first=1,
                         stop_spent=True)
            got = sim.run(240)
        lines = [l for l in got.lines if "TRAIN-WINDOW" in l]
        self.assertEqual(got.broken, [], figures(got))
        self.assertTrue(any("TRAIN-LEFT #2" in l for l in got.lines),
                        "\n".join(got.lines))
        self.assertIn("TRAIN-WINDOW: 4 -> 2: a member left a started "
                      "train", "\n".join(lines), "\n".join(got.lines))
        self.assertTrue(any("TRAIN-MERGE #3..#4" in l for l in got.lines),
                        "\n".join(got.lines))
        # #3..#4 were what was left of #2's train: no growth for them;
        # #2's own train of one merging whole grows it.
        self.assertEqual(lines[1:], [l for l in lines[1:]
                                     if "-> 3: a train merged whole" in l],
                         "\n".join(got.lines))
        self.assertEqual(len(lines), 2, "\n".join(got.lines))
        self.assertEqual(got.merged, 5, figures(got))

    def test_without_it_nothing_changes(self):
        m = ms.load_merger()
        m.configure(ms.project({"own_head_green": False}, batch=4,
                               batches=1, train_merge=True))
        scenario = [(0, "open", (n,)) for n in (1, 2, 3, 4)] + [
            (5, "push", (2,)), (7, "review", (2,))]
        with tempfile.TemporaryDirectory() as root:
            got = ms.Sim(m, root, scenario, own_minutes=40, later_first=1,
                         stop_spent=True).run(240)
        self.assertFalse(any("TRAIN-WINDOW" in l for l in got.lines))


class BoardUnreviewed(unittest.TestCase):
    """designs/0056, item 3: with merge.resolve.board_unreviewed, an
    author's resolution of a conflict with main that changes nothing
    outside its conflicted hunks boards a train, as its last member,
    while its reviewer is asked to read the hunks; the members ahead of
    it merge on the top's green, and it merges only after its new
    approval. One that changes a line outside the hunks doesn't board
    until it is approved."""

    def run_scenario(self, outside=False, board=True):
        m = ms.load_merger()
        merge = {"own_head_green": False}
        if board:
            merge["resolve"] = {"board_unreviewed": True}
        m.configure(ms.project(merge, batch=4, batches=1,
                               train_merge=True))
        shared = ("s.txt",)
        # #1 and #2 write the same line of s.txt: once #1 merges, #2
        # conflicts with main and goes back to its author, who resolves
        # it at minute 60, as #3 and #4 are opened. Its reviewer
        # approves the resolution at minute 150.
        scenario = [
            (0, "open", (1, None, False, False, False, True, None, False,
                         shared)),
            (0, "open", (2, None, False, False, False, True, None, False,
                         shared)),
            (60, "resolve_by_hand", (2, outside)),
            (60, "open", (3,)), (60, "open", (4,)),
            (150, "review", (2,))]
        with tempfile.TemporaryDirectory() as root:
            sim = ms.Sim(m, root, scenario, own_minutes=40, later_first=1,
                         stop_spent=True)
            got = sim.run(320, whole=True)
            merges = {x["pr"]: x["at"][11:16] for x in sim.world.merges}
            order = [x["pr"] for x in sim.world.merges]
        return got, merges, order

    def test_it_boards_last_and_merges_after_its_approval(self):
        got, merges, order = self.run_scenario()
        text = "\n".join(got.lines)
        self.assertEqual(got.broken, [], figures(got) + "\n" + text)
        self.assertTrue(any("RESOLUTION-REVIEW #2" in l
                            for l in got.lines), text)
        self.assertTrue(any("TRAIN-CUT 1: #3 #4 #2" in l
                            for l in got.lines), text)
        self.assertTrue(any("TRAIN-UNDER-REVIEW #3..#2" in l
                            for l in got.lines), text)
        self.assertEqual(sorted(merges), [1, 2, 3, 4], text)
        self.assertLess(order.index(3), order.index(2), text)
        self.assertLess(order.index(4), order.index(2), text)
        self.assertGreaterEqual(merges[2], "10:30", text)
        # #3 and #4 merged on the top's green, before the approval.
        self.assertLess(merges[4], "10:30", text)

    def test_a_change_outside_the_hunks_does_not_board(self):
        got, merges, order = self.run_scenario(outside=True)
        text = "\n".join(got.lines)
        self.assertEqual(got.broken, [], figures(got) + "\n" + text)
        self.assertFalse(any("RESOLUTION-REVIEW" in l for l in got.lines),
                         text)
        self.assertFalse(any("TRAIN-CUT" in l and "#2" in l
                             and l < "10:30" for l in got.lines), text)
        self.assertGreaterEqual(merges[2], "10:30", text)

    def test_without_it_the_resolution_waits_for_its_review(self):
        got, merges, order = self.run_scenario(board=False)
        text = "\n".join(got.lines)
        self.assertEqual(got.broken, [], figures(got) + "\n" + text)
        self.assertFalse(any("RESOLUTION-REVIEW" in l
                             or "TRAIN-UNDER-REVIEW" in l
                             for l in got.lines), text)
        self.assertGreaterEqual(merges[2], "10:30", text)


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

    def started(self):
        """A train of #1 then #2, started now."""
        w = self.world
        w.open(1)
        w.open(2)
        self.sim.monitor.trains["t" * 40] = {
            "members": [(1, "t1"), (2, "t2")], "since": ms.iso(w.now),
            "tree": "t2", "done": False}
        w.now += ms.datetime.timedelta(minutes=1)

    def rule5(self):
        return [l for l in self.sim.monitor.broken if "rule 5" in l]

    def test_a_departure_is_counted_by_place_once(self):
        self.started()
        minute = ms.iso(self.world.now)[11:16]
        self.sim.lines += [minute + " TRAIN-LEFT #2: pushed abc mid-train",
                           minute + " TRAIN-LEFT #2: pushed abc mid-train"]
        self.sim.monitor.count_departures()
        self.assertEqual(self.sim.monitor.departures,
                         {"first": 0, "middle": 0, "last": 1})

    def test_merging_past_one_ahead_in_the_train_is_a_break(self):
        self.started()
        self.merge(2)
        self.assertEqual(len(self.rule5()), 1, self.sim.monitor.broken)

    def test_not_past_one_whose_author_pushed_after_it_started(self):
        # 2026-11-13 replayed: #10942 left its train on a push, and the
        # rest of it merged as a train of two.
        self.started()
        self.world.push(1)
        self.merge(2)
        self.assertEqual(self.rule5(), [])

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
