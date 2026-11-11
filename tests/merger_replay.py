"""A real day replayed through the Merger simulation (docs/merger-sim.md,
"Replaying a real day").

    python3 -m tests.merger_replay TRACE [--config FILE] [--set K=V ...]
        [--compare K=V[,K=V...]] [--idle N] [--json]

TRACE comes from `tests/merger_trace.py`. `--config` is a JSON file
with the project's `merge` section (and, optionally, `checks`' `slots`
and `max_wait_minutes`): the settings replayed. `--set` changes one,
by its path under `merge` (`test_ahead.later_first=2`, `checks.slots=8`
for the slots). `--compare` replays the same day a second time with
those changes too, and prints both beside what really happened.

The pull requests arrive, are approved, pushed to, handed back and
fixed when the trace says; everything else is the simulation's own
doing: the real Merger's ticks, a model of the build check in the
trace's slots (with the day's other runs, the default branch's and
those of pull requests never approved, in them too), and its authors
answering a conflict with the default branch `resolve_minutes` after it
appears."""

import argparse
import copy
import datetime
import json
import os
import statistics
import sys
import tempfile
import time

from agent_forge import config
from tests import merger_sim as ms
from tests.helpers import example

# The Merger's settings the simulation can serve. The others need what
# the simulated GitHub doesn't keep, and are left out (and said so).
KEPT = {"merger", "ci_workflow", "stall_minutes", "test_ahead",
        "hold_conflicting", "yield_after_hand_back", "yield_cap",
        "yield_ignores_conflicts", "update_once", "right_of_way",
        "age_first", "early_conflicts", "stacks", "own_head_green",
        "hand_back_once_per_head"}


class ReplayWorld(ms.World):
    """The World, with the arrivals a trace describes: a pull request
    stacked on one not yet open opens that one first, unapproved, and
    one stacked on one already merged or closed opens on main."""

    def arrive(self, number, base=None, mac=False, red=False,
               urgent=False, shared=(), approve=True):
        pr = self.prs.get(number)
        if pr is not None:
            if pr["state"] == "OPEN" and approve \
                    and pr["approved"] != pr["version"]:
                self.review(number)
            return
        if base is not None:
            under = self.prs.get(base)
            if under is None:
                self.arrive(base, approve=False)
                under = self.prs[base]
            if under["state"] != "OPEN":
                base = None
        self.open(number, base, mac, red, urgent, approve, shared)

    def approve(self, number):
        pr = self.prs.get(number)
        if pr is not None and pr["state"] == "OPEN" \
                and pr["approved"] != pr["version"]:
            self.review(number)

    def author_push(self, number):
        pr = self.prs.get(number)
        if pr is not None and pr["state"] == "OPEN":
            self.push(number)

    def author_fix(self, number):
        pr = self.prs.get(number)
        if pr is not None and pr["state"] == "OPEN" and pr["red"]:
            self.fix(number)
            self.review(number)


def shared_files(number, conflicts):
    return tuple("c-%d-%d.txt" % tuple(sorted((number, other)))
                 for other in conflicts)


def scenario(trace):
    """[(minute, World method, args)] for the trace's pull requests."""
    s = []
    prs = trace["prs"]
    for key, pr in prs.items():
        n = int(key)
        base = pr.get("base")
        if base is not None and str(base) not in prs:
            base = None
        args = (n, base, bool(pr.get("mac")), pr.get("red") is not None
                and pr.get("red") <= pr["opened"] + 1,
                bool(pr.get("urgent")),
                shared_files(n, pr.get("conflicts") or ()))
        first = pr["approved"][0] if pr.get("approved") else None
        if first is not None and first <= pr["opened"]:
            s.append((first, "arrive", args))
        else:
            s.append((pr["opened"], "arrive", args + (False,)))
            if first is not None:
                s.append((first, "approve", (n,)))
        for m in pr.get("pushes") or ():
            if first is None or m < first:
                s.append((m, "author_push", (n,)))
        for left, back in pr.get("gaps") or ():
            s.append((left, "author_push", (n,)))
            if back is not None:
                s.append((back, "approve", (n,)))
        for at, pushed, approved in pr.get("handed_back") or ():
            s.append((pushed if pushed is not None else at,
                      "author_push", (n,)))
            if approved is not None:
                s.append((approved, "approve", (n,)))
        if pr.get("fixed") is not None:
            s.append((pr["fixed"], "author_fix", (n,)))
    # An arrival before what is stacked on it; otherwise by minute.
    order = {"arrive": 0, "author_push": 1, "author_fix": 2, "approve": 3}
    s.sort(key=lambda e: (e[0], order[e[1]], e[2][0]))
    return s


def settings(base_config, changes):
    """The `merge` section (and checks' slots and max wait) replayed:
    `base_config` with `changes` ({"test_ahead.later_first": 2, ...})
    applied, and what the simulation can't serve dropped."""
    merge = copy.deepcopy((base_config or {}).get("merge") or {})
    checks = copy.deepcopy((base_config or {}).get("checks") or {})
    for path, value in (changes or {}).items():
        target = merge
        parts = path.split(".")
        if parts[0] == "checks":
            target, parts = checks, parts[1:]
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    dropped = sorted(set(merge) - KEPT)
    merge = {k: v for k, v in merge.items() if k in KEPT}
    merge["gate"] = []
    # The simulated GitHub has no Actions: no workflow to wait for.
    merge["ci_workflow"] = ""
    merge.setdefault("merger", "Merger")
    merge.setdefault("test_ahead", {})
    return merge, checks, dropped


def project(merge):
    data = example()
    data["merge"] = merge
    data.setdefault("labels", {})["urgent"] = ms.URGENT
    data["checks"]["instances"] = {"mac": {
        "context": ms.GATED, "poster": "Merger", "gate": True,
        "steps": [{"name": "t", "run": ["true"]}],
        "pull_requests": {"paths": ["mac/*"]}}}
    return config.Project(data, None)


class Resolver:
    """Authors answer a conflict with the default branch: `delay`
    minutes after a pull request that can conflict first conflicts with
    main, its author merges main into it, and it is approved again."""

    def __init__(self, world, numbers, delay):
        self.world, self.numbers = world, set(numbers)
        self.delay = datetime.timedelta(minutes=delay)
        self.since = {}
        self.main = None

    def __call__(self):
        w = self.world
        main = w.main()
        if main != self.main:
            self.main = main
            for n in self.numbers:
                pr = w.prs.get(n)
                if pr is None or pr["state"] != "OPEN" \
                        or pr["base"] != "main" or n in self.since:
                    continue
                if w.merge_tree(main, pr["head"]) is None:
                    self.since[n] = w.now
        for n, when in list(self.since.items()):
            pr = w.prs[n]
            if pr["state"] != "OPEN":
                del self.since[n]
            elif w.now - when >= self.delay:
                del self.since[n]
                # Only if it still conflicts: the Merger, or its own
                # train, may have dealt with it meanwhile.
                if w.merge_tree(w.main(), pr["head"]) is None:
                    w.resolve(n)
                    w.review(n)


def replay(trace, base_config=None, changes=None, merger=None, idle=10,
           inspect=None):
    """Replays `trace` with the settings `base_config` and `changes`
    give: a dict of the figures (see `report`). `inspect(sim)`, when
    given, is called once the day is over, before the simulation's
    repositories go."""
    merge, checks, dropped = settings(base_config, changes)
    m = merger or ms.load_merger()
    m.configure(project(merge))
    ahead = merge.get("test_ahead") or {}
    slots = checks.get("slots") or trace.get("slots") or 4
    minutes = trace["run_minutes"]
    per_pr = {int(k): p.get("run_minutes") for k, p in trace["prs"].items()}

    def length(pr, top):
        if top:
            return minutes["top"]
        return per_pr.get(pr) or minutes["head"]

    started = time.monotonic()
    with tempfile.TemporaryDirectory() as root:
        sim = ms.Sim(m, root, scenario(trace), world=ReplayWorld,
                     slots=slots, minutes=length,
                     mac_minutes=trace.get("mac_minutes") or 25,
                     later_first=ahead.get("later_first", 0),
                     max_wait=checks.get("max_wait_minutes"),
                     stop_spent=bool(ahead.get("stop_spent")),
                     background=trace.get("background") or ())
        conflicting = [int(k) for k, p in trace["prs"].items()
                       if p.get("conflicts")]
        sim.each_minute.append(Resolver(sim.world, conflicting,
                                        trace.get("resolve_minutes") or 60))
        result = sim.run(trace["minutes"], whole=True, idle=idle)
        wall = time.monotonic() - started
        if inspect is not None:
            inspect(sim)
        return report(trace, sim, result, slots, wall, dropped,
                      merge, checks)


def per_hour(merges, start_minute=0, minutes=1440):
    hours = [0] * max(1, -(-minutes // 60))
    for _n, at in merges:
        if 0 <= at < minutes:
            hours[at // 60] += 1
    return hours


def report(trace, sim, result, slots, wall, dropped, merge, checks):
    w = sim.world
    start = ms.START
    merged = [(m_["pr"], int((ms.parse(m_["at"]) - start).total_seconds()
                              // 60)) for m_ in w.merges]
    waits = []
    for n, at in merged:
        approved = (trace["prs"].get(str(n)) or {}).get("approved") or []
        if approved:
            waits.append(at - approved[0])
    hours = trace["minutes"] / 60
    return {
        "settings": {"test_ahead": merge.get("test_ahead"),
                     "slots": slots,
                     "max_wait_minutes": checks.get("max_wait_minutes")},
        "dropped": dropped,
        "merged": len(merged),
        "merges": merged,
        "per_hour": round(len(merged) / hours, 2),
        "hourly": per_hour(merged, 0, trace["minutes"]),
        "runs": result.runs, "top_runs": result.top_runs,
        "runs_per_merge": round(result.runs / max(len(merged), 1), 2),
        "background_runs": sim.check.background_runs,
        "stopped": sim.check.stopped,
        "median_wait_minutes": round(statistics.median(waits), 1)
        if waits else None,
        "slot_busy": round(sim.check.busy_minutes
                           / max(1, slots * trace["minutes"]), 3),
        "broken": result.broken, "recuts": len(result.recuts),
        "ticks": sim.ticks, "wall_seconds": round(wall, 1)}


def reality(trace):
    real = trace["real"]
    merges = real["merges"]
    hours = trace["minutes"] / 60
    runs = real["head_runs"] + real["top_runs"] + real.get("stopped_runs", 0)
    waits = []
    for n, at in merges:
        approved = (trace["prs"].get(str(n)) or {}).get("approved") or []
        if approved:
            waits.append(at - approved[0])
    return {"merged": len(merges), "per_hour": round(len(merges) / hours, 2),
            "hourly": per_hour(merges, 0, trace["minutes"]),
            "runs": runs, "top_runs": real["top_runs"],
            "stopped": real.get("stopped_runs"),
            "runs_per_merge": round(runs / max(len(merges), 1), 2),
            "median_wait_minutes": round(statistics.median(waits), 1)
            if waits else None}


ROWS = (("merged", "merges"), ("per_hour", "merges an hour"),
        ("runs", "build runs (heads and tops)"),
        ("top_runs", "of them train tops"),
        ("runs_per_merge", "runs a merge"),
        ("median_wait_minutes", "median approval to merge, min"),
        ("slot_busy", "slots busy"), ("stopped", "runs stopped as spent"),
        ("recuts", "started trains re-cut"), ("broken", "rule breaks"),
        ("wall_seconds", "wall time, s"))


def table(columns):
    """Rows of the figures, one column per (title, report)."""
    width = max(len(label) for _k, label in ROWS) + 2
    lines = ["%-*s" % (width, "") + "".join("%-16s" % t for t, _r in columns)]
    for key, label in ROWS:
        cells = []
        for _t, r in columns:
            value = r.get(key, "")
            if isinstance(value, list):
                value = len(value)
            cells.append("%-16s" % ("" if value is None else value))
        lines.append("%-*s" % (width, label) + "".join(cells))
    lines.append("")
    for title, r in columns:
        if r.get("hourly"):
            lines.append("%-*s%s" % (width, "hourly, " + title,
                                     " ".join("%2d" % h for h in r["hourly"])))
    return "\n".join(lines)


def parse_changes(items):
    changes = {}
    for item in items or ():
        for part in item.split(","):
            key, _, value = part.partition("=")
            try:
                changes[key.strip()] = json.loads(value)
            except ValueError:
                changes[key.strip()] = value
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("trace")
    parser.add_argument("--config", help="JSON with the merge section")
    parser.add_argument("--set", action="append", default=[],
                        help="K=V, a setting under merge (checks.K too)")
    parser.add_argument("--compare", action="append",
                        help="K=V[,K=V]: replay again with these as well")
    parser.add_argument("--idle", type=int, default=10,
                        help="minutes a quiet stretch may skip ticks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    with open(args.trace) as handle:
        trace = json.load(handle)
    base = {}
    if args.config:
        with open(args.config) as handle:
            base = json.load(handle)
    first = parse_changes(args.set)
    out = {"reality": reality(trace),
           "replay": replay(trace, base, first, idle=args.idle)}
    if args.compare:
        out["compare"] = replay(trace, base, dict(
            first, **parse_changes(args.compare)), idle=args.idle)
    if args.json:
        json.dump(out, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    columns = [("reality", out["reality"]), ("replay", out["replay"])]
    if "compare" in out:
        columns.append(("compare", out["compare"]))
    print(table(columns))
    dropped = out["replay"]["dropped"]
    if dropped:
        print("\nsettings the simulation can't serve, left out: "
              + ", ".join(dropped))
    for line in trace.get("inferred") or ():
        print("inferred: " + line)
    for key in ("replay", "compare"):
        for line in (out.get(key) or {}).get("broken") or ():
            print("%s: %s" % (key, line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
