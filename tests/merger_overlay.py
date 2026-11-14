"""Traces overlaid into one heavier day, for the Merger simulation's
replay (docs/merger-sim.md, "Replaying a real day").

    python3 -m tests.merger_overlay TRACE [TRACE ...] [--copies N]
        [--shift MINUTES] > heavier.json

Each trace after the first (or each copy of one, with `--copies`) has
its pull requests renumbered past the ones before it and its minutes
moved `--shift` later (by default 0, the days on top of each other).
Their `files` keep their paths, so where two days' pull requests
change the same lines, the replay finds them conflicting, as git
would: conflicts grow with the load, not only within each day. The
recorded `conflicts` are kept within each day. A trace without
`files` (no `--repo` when it was taken) adds no conflicts across days,
and the result says so in `inferred`."""

import argparse
import copy
import json
import sys


def _moved(value, shift):
    if value is None:
        return None
    if isinstance(value, list):
        return [_moved(v, shift) for v in value]
    return value + shift


def renumbered(trace, offset, shift):
    """`trace` with each pull request numbered `offset` higher and every
    minute `shift` later."""
    out = copy.deepcopy(trace)
    prs = {}
    for key, pr in trace["prs"].items():
        pr = copy.deepcopy(pr)
        for field in ("opened", "red", "fixed", "merged"):
            if field in pr:
                pr[field] = _moved(pr[field], shift)
        for field in ("approved", "pushes", "gaps", "handed_back"):
            if field in pr:
                pr[field] = _moved(pr[field], shift)
        if pr.get("base") is not None:
            pr["base"] = pr["base"] + offset
        if pr.get("conflicts"):
            pr["conflicts"] = [n + offset for n in pr["conflicts"]]
        prs[str(int(key) + offset)] = pr
    out["prs"] = prs
    out["background"] = [dict(b, at=b["at"] + shift)
                         for b in trace.get("background") or ()]
    real = out.get("real") or {}
    real["merges"] = [[n + offset, m + shift]
                      for n, m in real.get("merges") or ()]
    out["real"] = real
    out["minutes"] = trace["minutes"] + shift
    return out


def overlay(traces, shift=0):
    """One trace of `traces` on top of each other, the k-th renumbered
    past the ones before it and `k * shift` minutes later."""
    if not traces:
        raise ValueError("no traces")
    parts, offset = [], 0
    for k, trace in enumerate(traces):
        parts.append(renumbered(trace, offset, k * shift))
        offset += max([int(n) for n in trace["prs"]] or [0])
    first = parts[0]
    out = copy.deepcopy(first)
    out["minutes"] = max(p["minutes"] for p in parts)
    out["prs"], out["background"] = {}, []
    real = {"merges": []}
    for p in parts:
        out["prs"].update(p["prs"])
        out["background"] += p["background"]
        for key, value in (p.get("real") or {}).items():
            if key == "merges":
                real["merges"] += value
            elif isinstance(value, (int, float)):
                real[key] = real.get(key, 0) + value
    out["background"].sort(key=lambda b: b["at"])
    real["merges"].sort(key=lambda m: m[1])
    out["real"] = real
    without = sum(1 for p in parts if not any(
        pr.get("files") for pr in p["prs"].values()))
    out["inferred"] = list(first.get("inferred") or []) + [
        "%d traces overlaid%s, renumbered past each other" % (
            len(parts), ", each %d min after the one before" % shift
            if shift else "")]
    if without:
        out["inferred"].append(
            "%d of them have no files: no conflicts across days for "
            "their pull requests" % without)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("traces", nargs="+")
    parser.add_argument("--copies", type=int, default=1,
                        help="each trace this many times over")
    parser.add_argument("--shift", type=int, default=0,
                        help="minutes each later trace starts after "
                        "the one before")
    args = parser.parse_args(argv)
    traces = []
    for path in args.traces:
        with open(path) as handle:
            trace = json.load(handle)
        traces += [trace] * max(1, args.copies)
    json.dump(overlay(traces, args.shift), sys.stdout, indent=1,
              sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
