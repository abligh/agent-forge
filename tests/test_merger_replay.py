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
import subprocess
import tempfile
import unittest

from tests import merger_overlay as mo
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


class LoneMerges(unittest.TestCase):
    """designs/0056: merges not in a train of two or more."""

    def test_own_runs_and_trains_of_one_are_lone(self):
        merges = [{"pr": 1}, {"pr": 2, "train": 10}, {"pr": 3, "train": 11},
                  {"pr": 4, "train": 11}]
        self.assertEqual(mr.lone_merges(merges), 2)


class Replay(unittest.TestCase):

    SETTINGS = {"merge": {"test_ahead": {
        "batch": 4, "batches": 2, "trains": "live", "stacks": True,
        "stop_spent": True, "later_first": 1}},
        "checks": {"max_wait_minutes": 30}}

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
        # #391's summary: red tops and departures from started trains.
        self.assertEqual((got["red_tops"], got["left_trains"]), (0, 0), got)

    def test_the_day_replayed_with_train_merge(self):
        # #391: a train merges as one pull request in a replay too.
        t = trace_of_day()
        seen = {}
        got = mr.replay(t, {"merge": {"own_head_green": False,
                                      "test_ahead": {
            "batch": 4, "batches": 2, "trains": "live", "stacks": True,
            "stop_spent": True, "later_first": 1, "train_merge": True}},
            "checks": {"max_wait_minutes": 30}}, idle=5,
            inspect=lambda sim: seen.update(trains=dict(sim.github.trains)))
        self.assertEqual(got["broken"], [])
        self.assertEqual(got["merged"], 5, got)
        # This morning's pull requests come one at a time, so its trains
        # are short, but each of the five went in through one.
        merged = sorted(n for tr in seen["trains"].values()
                        if tr["state"] == "MERGED" for n in tr["members"])
        self.assertEqual(merged, [1, 2, 3, 4, 5], seen["trains"])

    def test_the_day_replayed_behind_the_train_gate(self):
        # designs/0055: heads judged by quick runs, only trains merge,
        # each on its top's full green; a lone pull request goes in as a
        # train of one.
        t = trace_of_day()
        seen = {}
        got = mr.replay(t, {"merge": {"gate_tier": "train",
                                      "test_ahead": {
            "batch": 4, "batches": 2, "trains": "live", "stacks": True,
            "stop_spent": True, "later_first": 1, "train_merge": True}},
            "checks": {"max_wait_minutes": 30,
                       "quick": {"slots": 1, "minutes": 3}}}, idle=5,
            inspect=lambda sim: seen.update(merges=list(sim.world.merges)))
        self.assertEqual(got["broken"], [])
        self.assertEqual(got["merged"], 5, got)
        self.assertEqual(got["dropped"], [])
        self.assertGreaterEqual(got["quick_runs"], 5, got)
        self.assertTrue(all(m_.get("train") is not None
                            for m_ in seen["merges"]), seen["merges"])

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


class RealLines(unittest.TestCase):
    """#391 item 4: a trace's changed lines, and conflicts git decides
    from them."""

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", repo] + list(args), check=True,
                              capture_output=True, text=True,
                              env=ms.git_env()).stdout.strip()

    def test_a_heads_changed_lines(self):
        with tempfile.TemporaryDirectory() as repo:
            self.git(repo, "init", "-q", "-b", "main")
            with open(os.path.join(repo, "a.txt"), "w") as handle:
                handle.write("".join("%d\n" % k for k in range(1, 21)))
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "base")
            self.git(repo, "branch", "start")
            self.git(repo, "checkout", "-qb", "pr")
            with open(os.path.join(repo, "a.txt")) as handle:
                lines = handle.read().splitlines()
            lines[4:6] = ["five", "six"]
            lines[14:14] = ["new"]
            with open(os.path.join(repo, "a.txt"), "w") as handle:
                handle.write("\n".join(lines) + "\n")
            with open(os.path.join(repo, "b.txt"), "w") as handle:
                handle.write("b\n")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "change")
            head = self.git(repo, "rev-parse", "HEAD")
            self.assertEqual(mt.hunks_of(repo, head[:10], "start"),
                             {"a.txt": [[5, 2], [14, 0]], "b.txt": [[0, 0]]})
            self.assertIsNone(mt.hunks_of(repo, "f" * 10, "start"))

    def test_the_trace_takes_the_last_heads_lines(self):
        merger, verdicts = day_logs()
        asked = []

        def files_of(sha):
            asked.append(sha)
            return {"x.rs": [[1, 1]]} if sha == "5b" * 5 else None
        t = mt.extract(merger, verdicts, T0,
                       T0 + datetime.timedelta(minutes=90), slots=3,
                       files_of=files_of)
        self.assertEqual(t["prs"]["5"]["files"], {"x.rs": [[1, 1]]})
        self.assertNotIn("5a" * 5, asked)
        self.assertNotIn("files", t["prs"]["1"])
        self.assertTrue(any("files" in line for line in t["inferred"]))

    def test_a_day_with_real_lines_keeps_every_rule(self):
        t = trace_of_day()
        # #1 and #5 change the same lines (the logs never said they
        # conflict), #3 other lines of that file.
        self.assertFalse(t["prs"]["5"].get("conflicts"))
        t["prs"]["1"]["files"] = {"core.rs": [[10, 4]]}
        t["prs"]["5"]["files"] = {"core.rs": [[12, 1]]}
        t["prs"]["3"]["files"] = {"core.rs": [[40, 2]]}
        seen = {}
        got = mr.replay(t, Replay.SETTINGS, idle=5, inspect=lambda sim:
                        seen.update(events=list(sim.world.events)))
        self.assertEqual(got["broken"], [])
        self.assertEqual(got["merged"], 5, got)
        self.assertIn(5, {e["pr"] for e in seen["events"]
                          if e["what"] == "resolve"})

    def opened(self, changed):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        w = mr.ReplayWorld(tmp.name)
        w.with_files(changed)
        for n in changed:
            w.arrive(n)
        return w

    def test_overlapping_lines_conflict(self):
        w = self.opened({1: {"s.rs": [[10, 3]]}, 2: {"s.rs": [[11, 1]]}})
        self.assertIsNone(w.merge_tree(w.prs[1]["head"], w.prs[2]["head"]))

    def test_lines_apart_in_one_file_do_not(self):
        w = self.opened({1: {"s.rs": [[10, 3]]}, 2: {"s.rs": [[30, 1]]},
                         3: {"t.rs": [[10, 3]]}})
        self.assertIsNotNone(
            w.merge_tree(w.prs[1]["head"], w.prs[2]["head"]))
        self.assertIsNotNone(
            w.merge_tree(w.prs[1]["head"], w.prs[3]["head"]))


class Overlay(unittest.TestCase):
    """#391 item 4: days overlaid into a heavier one, whose pull
    requests' lines meet across days."""

    def test_the_second_day_is_renumbered_and_moved(self):
        t = trace_of_day()
        top = max(int(n) for n in t["prs"])
        got = mo.overlay([t, t], shift=30)
        self.assertEqual(len(got["prs"]), 2 * len(t["prs"]))
        one, two = t["prs"]["2"], got["prs"][str(2 + top)]
        self.assertEqual(two["opened"], one["opened"] + 30)
        self.assertEqual(two["base"], one["base"] + top)
        self.assertEqual(two["approved"], [m + 30 for m in one["approved"]])
        self.assertEqual(got["prs"][str(3 + top)]["conflicts"],
                         [n + top for n in t["prs"]["3"]["conflicts"]])
        self.assertEqual(len(got["real"]["merges"]),
                         2 * len(t["real"]["merges"]))
        self.assertEqual(got["minutes"], t["minutes"] + 30)
        self.assertTrue(any("no files" in l for l in got["inferred"]))

    def test_two_days_lines_conflict_across_days(self):
        t = trace_of_day()
        t["prs"]["5"]["files"] = {"core.rs": [[10, 4]]}
        top = max(int(n) for n in t["prs"])
        heavier = mo.overlay([t, t], shift=5)
        self.assertFalse(heavier["prs"][str(5 + top)].get("conflicts"))
        # Twice the load: time for all of it to go in.
        heavier["minutes"] = 300
        seen = {}
        got = mr.replay(heavier, Replay.SETTINGS, idle=5,
                        inspect=lambda sim: seen.update(
                            events=list(sim.world.events)))
        self.assertEqual(got["broken"], [])
        self.assertEqual(got["merged"], 2 * mr.replay(
            t, Replay.SETTINGS, idle=5)["merged"], got)
        # The second day's #5 changes the first day's #5's lines.
        self.assertIn(5 + top, {e["pr"] for e in seen["events"]
                                if e["what"] == "resolve"})


class AfterTheSimulatedCheck(unittest.TestCase):
    """--approve-after-green and --fix-after-red (#391 item 3): the
    reviewer and the authors answer the simulated check, not the
    trace's clock."""

    SETTINGS = {"merge": {"test_ahead": {
        "batch": 4, "batches": 2, "trains": "live", "stacks": True,
        "stop_spent": True, "later_first": 1}},
        "checks": {"max_wait_minutes": 30}}

    def test_no_approval_or_fix_comes_from_the_trace(self):
        t = trace_of_day()
        s = mr.scenario(t, approve_after=5, fix_after=3)
        self.assertFalse([e for e in s if e[1] in ("approve", "author_fix")])
        self.assertTrue(all(e[2][-1] is False for e in s
                            if e[1] == "arrive"))
        self.assertTrue([e for e in mr.scenario(t) if e[1] == "approve"])

    def test_approvals_follow_the_green_and_fixes_the_red(self):
        t = trace_of_day()
        seen = {}

        def inspect(sim):
            w = sim.world
            seen["events"] = list(w.events)
            seen["green"] = {
                sha: [st for st in reversed(sts)
                      if st["context"] == ms.CONTEXT
                      and st["state"] == "success"]
                for sha, sts in w.statuses.items()}
        got = mr.replay(t, self.SETTINGS, idle=5, inspect=inspect,
                        approve_after=5, fix_after=3)
        self.assertEqual(got["broken"], [])
        self.assertEqual(got["merged"], 5, got)
        approvals = [e for e in seen["events"] if e["what"] == "approve"]
        # #6 the trace never approved, and nor does the reviewer here.
        self.assertNotIn(6, {e["pr"] for e in approvals})
        self.assertEqual({e["pr"] for e in approvals}, {1, 2, 3, 4, 5})
        fixes = [e for e in seen["events"] if e["what"] == "fix"]
        self.assertEqual([e["pr"] for e in fixes], [5])
        # Approved no sooner than the check's run and five minutes.
        self.assertGreaterEqual(got["median_open_to_approval_minutes"],
                                5 + t["run_minutes"]["head"] // 2)
        self.assertIsNotNone(got["median_approval_to_merge_minutes"])

    def test_the_report_splits_the_waits(self):
        t = trace_of_day()
        real = mr.reality(t)
        self.assertIn("median_open_to_approval_minutes", real)
        self.assertIn("median_approval_to_merge_minutes", real)
        text = mr.table([("reality", real)])
        self.assertIn("median open to approval, min", text)
        self.assertIn("median approval to merge, min", text)


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

    def check_with(self, train_slots, tops):
        check = ms.SimCheck(self.world, "/nonexistent", slots=2,
                            later_first=0, train_slots=train_slots)
        check.running = [{"sha": "r" * 40, "tree": "tr", "pr": 9,
                          "top": False, "done": ms.START}]
        check.queue = [{"sha": "h" * 40, "tree": "t", "pr": 1,
                        "top": False, "queued": ms.START}]
        # The batch's top has its verdict: only a later top waits.
        check.state["results"]["a" * 40] = {"ok": True, "tree": "ta"}
        return check.pick(tops, ms.START, check.own_full(tops))

    def test_train_slots_keep_a_slot_for_a_waiting_prediction(self):
        # A later top waits behind a head with later_first 0; with
        # train_slots the head can't have the last slot.
        tops = [{"sha": "a" * 40, "tree": "ta", "pr": 2},
                {"sha": "b" * 40, "tree": "tb", "pr": 3}]
        self.assertEqual(self.check_with(0, tops)["pr"], 1)
        self.assertEqual(self.check_with(1, tops)["pr"], 3)

    def test_with_no_prediction_waiting_nothing_is_kept(self):
        self.assertEqual(self.check_with(
            1, [{"sha": "a" * 40, "tree": "ta", "pr": 2}])["pr"], 1)

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
