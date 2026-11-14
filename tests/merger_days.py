"""The recorded-days library: made-up days, seeded, that every change
to the Merger's or the build check's order is replayed on
(docs/merger-sim.md, "Comparing two versions on the library's days").

    python3 -m tests.merger_days [--write]

A day is a trace in `tests/merger_trace.py`'s shape, made here from a
seed and a few figures, so no project's pull requests, names or times
are in it. Each kind is the shape of a day seen in practice:

    smoke     three hours, a dozen pull requests: small enough for the
              test suite
    ordinary  a working day: about 120 pull requests on 5 slots
    heavy     twice the arrivals on the same slots: the queue fills
    bad       an ordinary day's arrivals with three times the reds,
              conflicts and pushes after approval

`tests/days/<kind>.json` holds each, as `--write` makes them;
tests/test_merger_days.py checks that they still match."""

import argparse
import json
import os
import random
import sys

DAYS_DIR = os.path.join(os.path.dirname(__file__), "days")

# kind: (seed, minutes, pull requests, slots, the share of them red on
# arrival, stacked, pushed after approval, and conflicts each with one
# other near it in time).
KINDS = {
    "smoke": (1, 180, 12, 2, 0.1, 0.2, 0.1, 0.2),
    "ordinary": (2, 1440, 120, 5, 0.03, 0.25, 0.1, 0.3),
    "heavy": (3, 1440, 240, 5, 0.03, 0.25, 0.1, 0.3),
    # The ordinary day's seed: the same pull requests, more often red,
    # pushed and conflicting (every draw is made whether used or not,
    # so each of those is a superset of the ordinary day's).
    "bad": (2, 1440, 120, 5, 0.09, 0.25, 0.3, 0.9),
}
RUN_MINUTES = {"head": 20.0, "top": 21.0}


def arrival(rng, minutes):
    """A minute in the day, more often in its working hours (for a day,
    08:00 to 20:00 twice as often as the rest)."""
    if minutes < 1440:
        return rng.randrange(minutes)
    while True:
        m = rng.randrange(minutes)
        if 480 <= m < 1200 or rng.random() < 0.5:
            return m


def day(kind):
    """The trace of one kind of day: always the same for a kind."""
    seed, minutes, n, slots, red, stacked, pushed, conflict = KINDS[kind]
    rng = random.Random(seed)
    first = 1
    opened = sorted(arrival(rng, minutes) for _ in range(n))
    prs = {}
    for k, at in enumerate(opened):
        number = first + k
        approve = at + rng.randint(10, max(11, minutes // 6))
        pr = {"opened": at, "approved": [min(approve, minutes - 1)],
              "base": None, "conflicts": [], "fixed": None, "gaps": [],
              "handed_back": [], "mac": False, "merged": None,
              "pushes": [], "red": None,
              "run_minutes": RUN_MINUTES["head"], "urgent": False}
        earlier = [first + j for j in range(k) if at - opened[j] < 180]
        draws = [rng.random() for _ in range(7)]
        if earlier and draws[0] < stacked:
            pr["base"] = earlier[int(draws[1] * len(earlier))]
        if draws[2] < red:
            pr["red"] = at
            pr["fixed"] = min(at + 30 + int(draws[3] * 90), minutes - 1)
            pr["pushes"].append(pr["fixed"])
        if draws[4] < pushed and approve + 10 < minutes:
            left = min(approve + 5 + int(draws[5] * 115), minutes - 2)
            back = min(left + 5 + int(draws[6] * 25), minutes - 1)
            pr["gaps"].append([left, back])
        prs[str(number)] = pr
    numbers = sorted(prs, key=int)
    for k, key in enumerate(numbers):
        u, pick = rng.random(), rng.random()
        near = numbers[max(0, k - 8):k] + numbers[k + 1:k + 9]
        if u < conflict and near:
            other = near[int(pick * len(near))]
            prs[key]["conflicts"].append(int(other))
            prs[other]["conflicts"].append(int(key))
    for pr in prs.values():
        pr["conflicts"] = sorted(set(pr["conflicts"]))
    background = [{"at": m, "main": True, "minutes": RUN_MINUTES["head"]}
                  for m in range(0, minutes, 45)]
    return {"version": 1, "start": "2026-01-05T00:00:00Z",
            "minutes": minutes, "slots": slots, "mac_minutes": 25,
            "resolve_minutes": 30.0, "run_minutes": dict(RUN_MINUTES),
            "background": background, "prs": prs,
            # Nothing really happened: there is no reality to show.
            "real": {"merges": [], "head_runs": 0, "top_runs": 0,
                     "main_runs": 0, "background_runs": 0,
                     "stopped_runs": 0},
            "inferred": ["a made-up %s day (tests/merger_days.py)" % kind]}


def path(kind):
    return os.path.join(DAYS_DIR, kind + ".json")


def text(kind):
    return json.dumps(day(kind), indent=1, sort_keys=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="write tests/days/<kind>.json for each kind")
    args = parser.parse_args(argv)
    for kind in KINDS:
        if args.write:
            os.makedirs(DAYS_DIR, exist_ok=True)
            with open(path(kind), "w") as handle:
                handle.write(text(kind))
        t = day(kind)
        print("%-9s %4d pull requests over %d minutes on %d slots%s" % (
            kind, len(t["prs"]), t["minutes"], t["slots"],
            ", written" if args.write else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
