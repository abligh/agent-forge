"""Replaying a day through the Merger simulation (tests/merger_trace.py,
tests/merger_replay.py, docs/merger-sim.md): a trace taken from a
synthetic day's logs says what the logs say, the replay of it keeps
every rule, and the compare output sets the two settings side by side.
Also the parts of the simulation the replay added: forge-pr-check's
start order, and skipping quiet minutes without changing the result."""

import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest

from tests import merger_replay as mr
from tests import merger_sim as ms
from tests import merger_trace as mt

T0 = datetime.datetime(2026, 9, 1, 8, 0, 0, tzinfo=datetime.timezone.utc)


def at(minute, seconds=0):
    return mt.iso(T0 + datetime.timedelta(minutes=minute, seconds=seconds))


def tick(minute, queue=(), held=None, decisions=()):
    return json.dumps({"at": at(minute, 5), "queue": list(queue),
                       "held": held or {}, "decisions": list(decisions)})


def merge(pr):
    return {"action": "MERGE", "pr": pr, "result": "done", "head": "a" * 40}


def verdict(minute, pr, ok=True, ran=600, waited=60, top=False,
            sha="abc1234567"):
    return "%s VERDICT %s#%d %s: %s waited=%ds ran=%ds" % (
        at(minute), "AHEAD " if top else "", pr, sha,
        "green" if ok else "red(test-rust)", waited, ran)


def day_logs():
    """A morning's logs: #1 merges, #2 is stacked on it, #3 waits for
    #1 as it conflicts with it, #4 waits for the gated instance, #5's
    first head is red and its next green, #6 is never approved, and
    the default branch runs once."""
    merger = [tick(0, [1, 5], {"2": "stacked on #1",
                               "3": "conflicts with #1, ahead of it in "
                                    "the queue",
                               "4": "waiting for vm/mac (pending)"})]
    for m in range(1, 30):
        merger.append(tick(m, [1, 5, 4] if m > 20 else [1, 5],
                           {"2": "stacked on #1",
                            "3": "conflicts with #1, ahead of it in the "
                                 "queue"}))
    merger.append(tick(30, [2, 3, 4, 5], {}, [merge(1)]))
    merger.append(tick(31, [3, 4, 5], {}, [merge(2)]))
    for m in range(32, 60):
        merger.append(tick(m, [3, 4, 5]))
    merger.append(tick(60, [4, 5], {}, [merge(3)]))
    merger.append(tick(61, [5], {}, [merge(4)]))
    merger.append(tick(62, [], {}, [merge(5)]))
    verdicts = [
        verdict(12, 5, ok=False, sha="5a" * 5),
        verdict(25, 1, sha="1a" * 5),
        verdict(28, 6, sha="6a" * 5),
        verdict(29, 1, top=True, sha="7a" * 5),
        verdict(45, 5, sha="5b" * 5),
        "%s MAIN-GREEN main %s: green waited=30s ran=480s"
        % (at(40), "8a" * 5),
        "%s STOPPED-SPENT #3 %s: the Merger no longer predicts its tree"
        % (at(41), "3a" * 5),
    ]
    return merger, verdicts


def trace_of_day():
    merger, verdicts = day_logs()
    return mt.extract(merger, verdicts, T0,
                      T0 + datetime.timedelta(minutes=90), urgent=[4],
                      slots=3)


class Trace(unittest.TestCase):

    def test_what_the_logs_say(self):
        t = trace_of_day()
        prs = t["prs"]
        self.assertEqual(sorted(prs), ["1", "2", "3", "4", "5"])
        self.assertEqual(t["minutes"], 90)
        self.assertEqual(prs["2"]["base"], 1)
        self.assertTrue(prs["4"]["mac"])
        self.assertTrue(prs["4"]["urgent"])
        self.assertEqual(prs["3"]["conflicts"], [1])
        self.assertEqual(prs["1"]["conflicts"], [3])
        self.assertEqual(
            {n: p["merged"] for n, p in prs.items()},
            {"1": 30, "2": 31, "3": 60, "4": 61, "5": 62})
        # #5's first run, red, ended at 12 after 1 + 10 minutes.
        self.assertEqual(prs["5"]["red"], 1)
        self.assertEqual(prs["5"]["fixed"], 34)
        self.assertEqual(prs["5"]["opened"], 0)
        self.assertEqual(prs["1"]["run_minutes"], 10.0)
        # #6 was never approved, and the default branch ran once: both
        # are other work in the slots.
        self.assertEqual(t["background"], [
            {"at": 17, "minutes": 10.0, "main": False},
            {"at": 31, "minutes": 8.0, "main": True}])
        self.assertEqual(t["real"]["merges"],
                         [[1, 30], [2, 31], [3, 60], [4, 61], [5, 62]])
        self.assertEqual(t["real"]["top_runs"], 1)
        self.assertEqual(t["real"]["stopped_runs"], 1)
        self.assertEqual(t["real"]["head_runs"], 3)
        self.assertEqual(t["slots"], 3)
        self.assertTrue(any("approved when the window opened" in line
                            for line in t["inferred"]))

    def test_a_window_without_records_is_refused(self):
        with self.assertRaises(ValueError):
            mt.extract([], [], T0)

    def test_the_command_writes_json(self):
        merger, verdicts = day_logs()
        with tempfile.TemporaryDirectory() as tmp:
            m = os.path.join(tmp, "merger.jsonl")
            v = os.path.join(tmp, "verdicts.log")
            with open(m, "w") as handle:
                handle.write("\n".join(merger) + "\n")
            with open(v, "w") as handle:
                handle.write("\n".join(verdicts) + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                mt.main(["--merger", m, "--verdicts", v, "--since", at(0),
                         "--until", at(90), "--slots", "3"])
        self.assertEqual(json.loads(out.getvalue())["slots"], 3)


class Replay(unittest.TestCase):

    def test_the_day_replayed_keeps_every_rule(self):
        t = trace_of_day()
        got = mr.replay(t, {"merge": {"test_ahead": {
            "batch": 4, "batches": 2, "trains": "live", "stacks": True,
            "stop_spent": True, "later_first": 1}},
            "checks": {"max_wait_minutes": 30}}, idle=5)
        self.assertEqual(got["broken"], [])
        self.assertEqual(got["merged"], 5, got)
        self.assertEqual(got["settings"]["slots"], 3)
        self.assertEqual(got["dropped"], [])
        self.assertGreaterEqual(got["background_runs"], 1)

    def test_settings_left_out_are_said(self):
        merge, checks, dropped = mr.settings(
            {"merge": {"code_owners": True, "test_ahead": {"batch": 2}},
             "checks": {"slots": 5}},
            {"test_ahead.later_first": 2, "checks.slots": 6})
        self.assertEqual(dropped, ["code_owners"])
        self.assertEqual(merge["test_ahead"], {"batch": 2, "later_first": 2})
        self.assertEqual(merge["gate"], [])
        self.assertEqual(checks["slots"], 6)

    def test_compare_prints_both_beside_reality(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trace.json")
            with open(path, "w") as handle:
                json.dump(trace_of_day(), handle)
            cfg = os.path.join(tmp, "config.json")
            with open(cfg, "w") as handle:
                json.dump({"merge": {"test_ahead": {
                    "batch": 4, "batches": 2, "trains": "live",
                    "stacks": True}}}, handle)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                mr.main([path, "--config", cfg, "--idle", "5",
                         "--compare", "test_ahead.later_first=2"])
        text = out.getvalue()
        header = text.splitlines()[0].split()
        self.assertEqual(header, ["reality", "replay", "compare"])
        self.assertIn("merges an hour", text)
        self.assertIn("inferred:", text)


class CheckOrder(unittest.TestCase):
    """SimCheck in forge-pr-check's order: a later batch's top goes
    before an ordinary head only as `later_first` allows."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.world = ms.World(tmp.name)
        self.world.open(1)

    def picked(self, later_first):
        check = ms.SimCheck(self.world, "/nonexistent", slots=1,
                            later_first=later_first)
        check.queue = [{"sha": "h" * 40, "tree": "t", "pr": 1, "top": False,
                        "queued": ms.START}]
        tops = [{"sha": "a" * 40, "tree": "ta", "pr": 2},
                {"sha": "b" * 40, "tree": "tb", "pr": 3}]
        check.state["results"]["a" * 40] = {"ok": True, "tree": "ta"}
        return check.pick(tops, ms.START)["pr"]

    def test_none_first_waits_for_a_spare_slot(self):
        self.assertEqual(self.picked(0), 1)

    def test_one_first_goes_before_the_head(self):
        self.assertEqual(self.picked(1), 3)


class Leavers(unittest.TestCase):
    """A member out of the predictions for a tick, which then merges at
    the tree its train tested, lost nothing: not a break."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sim = ms.Sim(None, tmp.name, [])

    def test_one_that_merged_at_its_tree_is_not_a_break(self):
        self.sim.world.merges.append({"pr": 1, "tree": "t1"})
        self.sim.monitor.pending.append((1, "t1", "#1 left", "08:00 rule 1: "))
        self.sim.monitor.finish()
        self.assertEqual(self.sim.monitor.broken, [])

    def test_one_that_merged_elsewhere_is(self):
        self.sim.world.merges.append({"pr": 1, "tree": "t2"})
        self.sim.monitor.pending.append((1, "t1", "#1 left", "08:00 rule 1: "))
        self.sim.monitor.finish()
        self.assertEqual(self.sim.monitor.broken, ["08:00 rule 1: #1 left"])
        self.assertEqual(self.sim.monitor.left, 1)


class Idle(unittest.TestCase):

    def test_skipping_quiet_minutes_changes_nothing(self):
        def run(idle):
            m = ms.load_merger()
            m.configure(ms.project())
            with tempfile.TemporaryDirectory() as root:
                sim = ms.Sim(m, root, ms.day())
                got = sim.run(300, idle=idle)
                return [(x["pr"], x["at"]) for x in sim.world.merges], \
                    got.runs, sim.ticks
        every, runs, ticks = run(0)
        skipped, runs_skipped, fewer = run(10)
        self.assertEqual(skipped, every)
        self.assertEqual(runs_skipped, runs)
        self.assertLess(fewer, ticks)


if __name__ == "__main__":
    unittest.main()
