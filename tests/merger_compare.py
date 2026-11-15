"""Replay the library's days at two versions of agent-forge, and print
the comparison as a Markdown table for a pull request
(docs/merger-sim.md, "Comparing two versions on the library's days").

    python3 -m tests.merger_compare [--day KIND|TRACE ...]
        --before REF --after REF [--config FILE] [--set K=V ...]
        [--approve-after-green MINUTES] [--fix-after-red MINUTES]

A change to which pull request the Merger takes next, or to what the
build check runs first, carries this table in its pull request
(CLAUDE.md). Each REF is a git commit, checked out in a temporary
worktree, or `.` for the working tree as it is. The days are
`tests/merger_days.py`'s kinds (by default `ordinary`, `heavy` and
`bad`) or trace files. Each is replayed with
`tests/merger_replay.py` at both versions with the same settings and
the same PYTHONHASHSEED; the figures a version doesn't report show as
`-`. Make: `make replay-compare
BEFORE=origin/main AFTER=.`."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The figures compared: (the replay's key, the row's name).
FIGURES = (("merged", "merges"),
           ("median_approval_to_merge_minutes",
            "approval to merge, median min"),
           ("p90_approval_to_merge_minutes", "approval to merge, p90 min"),
           ("hand_backs", "hand-backs"),
           ("runs_per_merge", "build runs a merge"),
           ("red_tops", "train tops red"),
           ("departures_first", "left a started train: first"),
           ("departures_middle", "left a started train: middle"),
           ("departures_last", "left a started train: last"),
           ("lone_merges", "merged alone, not in a train"),
           ("broken", "rule breaks"))
DEFAULT_DAYS = ("ordinary", "heavy", "bad")
# Both sides replay with the same string hashing, so that nothing but
# the change differs between them. (A replay is the same every time it
# runs once the simulation's git dates are fixed, #413; a version from
# before that may vary a little from run to run.)
HASH_SEED = "0"


def trace_path(day):
    """A library kind's file, or `day` itself as a path."""
    from tests import merger_days
    if day in merger_days.KINDS:
        return merger_days.path(day)
    return os.path.abspath(day)


def checkout(ref, root, run=subprocess.run):
    """A directory holding agent-forge at `ref`: the working tree for
    `.`, else a detached worktree under `root`."""
    if ref == ".":
        return HERE
    where = os.path.join(root, "at-%s" % ref.replace("/", "_"))
    run(["git", "-C", HERE, "worktree", "add", "-q", "--detach", where,
         ref], check=True)
    return where


def replay_at(tree, trace, options, run=subprocess.run):
    """The replay's JSON report of `trace`, run from `tree`."""
    got = run([sys.executable, "-m", "tests.merger_replay", trace,
               "--json"] + options, cwd=tree, check=True,
              capture_output=True, text=True,
              env=dict(os.environ, PYTHONPATH=tree, PYTHONHASHSEED=HASH_SEED))
    return json.loads(got.stdout)["replay"]


def cell(value):
    if value is None:
        return "-"
    if isinstance(value, list):
        return str(len(value))
    return str(value)


def markdown(results, before, after):
    """The table: a row per figure, a before and an after column per
    day."""
    days = list(results)
    head = "| | " + " | ".join("%s: %s | %s: %s" % (d, before, d, after)
                                for d in days) + " |"
    lines = [head, "|---" * (1 + 2 * len(days)) + "|"]
    for key, name in FIGURES:
        cells = []
        for d in days:
            for side in ("before", "after"):
                cells.append(cell(results[d][side].get(key)))
        lines.append("| %s | %s |" % (name, " | ".join(cells)))
    return "\n".join(lines)


def compare(days, before, after, options, run=subprocess.run):
    """{day: {"before": report, "after": report}}."""
    root = tempfile.mkdtemp(prefix="merger-compare-")
    trees = []
    try:
        sides = {}
        for side, ref in (("before", before), ("after", after)):
            tree = checkout(ref, root, run)
            if tree != HERE:
                trees.append(tree)
            sides[side] = tree
        return {d: {side: replay_at(tree, trace_path(d), options, run)
                    for side, tree in sides.items()} for d in days}
    finally:
        for tree in trees:
            run(["git", "-C", HERE, "worktree", "remove", "--force", tree],
                check=False)
        shutil.rmtree(root, ignore_errors=True)


def main(argv=None, run=subprocess.run, out=print):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--day", action="append",
                        help="a library kind or a trace file (default: "
                        + ", ".join(DEFAULT_DAYS) + ")")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--config", help="JSON with the merge section")
    parser.add_argument("--set", action="append", default=[],
                        help="K=V, a setting under merge, at both")
    parser.add_argument("--approve-after-green", type=int, metavar="MINUTES")
    parser.add_argument("--fix-after-red", type=int, metavar="MINUTES")
    args = parser.parse_args(argv)
    options = []
    if args.config:
        options += ["--config", os.path.abspath(args.config)]
    for item in args.set:
        options += ["--set", item]
    if args.approve_after_green is not None:
        options += ["--approve-after-green", str(args.approve_after_green)]
    if args.fix_after_red is not None:
        options += ["--fix-after-red", str(args.fix_after_red)]
    results = compare(args.day or list(DEFAULT_DAYS), args.before,
                      args.after, options, run)
    out(markdown(results, args.before, args.after))
    return 0


if __name__ == "__main__":
    sys.exit(main())
