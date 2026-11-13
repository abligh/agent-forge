"""A trace of a real day for the Merger simulation's replay
(docs/merger-sim.md, "Replaying a real day").

    python3 -m tests.merger_trace --merger merger.jsonl \\
        --verdicts verdicts.log [--since ISO] [--until ISO] \\
        [--urgent N,N] [--slots N] > trace.json

It reads what a check host keeps, and nothing else:

- the Merger's tick records (`merger.jsonl` in its state directory):
  which pull requests were approved (its queue and its held ones), from
  when, which were stacked on which, which waited for a gated instance,
  which it held for conflicting with one ahead, what it handed back and
  why, and what it merged when;
- the build check's `verdicts.log`: each run's pull request, head, end,
  wait and length, red or green, the Merger's tops (`VERDICT AHEAD`)
  and the default branch's runs (`MAIN-GREEN`).

What the logs can't say, it infers and lists in the trace's
`inferred`: a conflict with the default branch is put down to the pull
request merged last before it was seen; a pull request first seen
already approved was approved when the window opened; the gated
instance's run length is a default. Labels aren't in the logs: pass
the urgent ones with `--urgent`.

The trace (`version` 1) is JSON:

    {"version": 1, "start": ISO, "minutes": N, "slots": N or null,
     "prs": {"<n>": {"opened": m, "approved": [m, ...], "pushes": [m],
                     "base": n or null, "mac": bool, "urgent": bool,
                     "gaps": [[m, m or null], ...],
                     "handed_back": [[m, pushed m or null,
                                      approved m or null], ...],
                     "red": m or null, "fixed": m or null,
                     "conflicts": [n, ...], "run_minutes": x or null,
                     "merged": m or null}},
     "background": [{"at": m, "minutes": x, "main": bool}],
     "run_minutes": {"head": x, "top": x},
     "resolve_minutes": x,
     "real": {"merges": [[n, m], ...], "head_runs": n, "top_runs": n,
              "main_runs": n, "stopped_runs": n, "background_runs": n},
     "inferred": ["...", ...]}

Minutes count from `start`. A real project's trace names its pull
requests and nothing else, but keep it out of any repository all the
same: it is that project's history."""

import argparse
import collections
import datetime
import json
import re
import statistics
import sys

VERDICT = re.compile(
    r"^(\S+) VERDICT (AHEAD )?#(\d+) ([0-9a-f]+): (green|red\(([^)]*)\))"
    r"(?:.*?waited=(\d+)s ran=(\d+)s)?")
MAIN = re.compile(r"^(\S+) MAIN-GREEN main ([0-9a-f]+): green "
                  r"waited=(\d+)s ran=(\d+)s")
STOPPED = re.compile(r"^(\S+) (?:STOPPED-SPENT|SUPERSEDED|STOPPED) #(\d+) ")
STACKED = re.compile(r"^stacked on #(\d+)")
AHEAD_OF = re.compile(r"^conflicts with #(\d+)")
GATED = "vm/mac"
MAC_MINUTES = 25


def parse_time(text):
    return datetime.datetime.strptime(text[:20], "%Y-%m-%dT%H:%M:%SZ") \
        .replace(tzinfo=datetime.timezone.utc)


def iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_records(lines, since=None, until=None):
    """The Merger's tick records in the window, oldest first."""
    out = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
            when = parse_time(record["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if (since and when < since) or (until and when >= until):
            continue
        out.append((when, record))
    out.sort(key=lambda r: r[0])
    return out


def read_runs(lines, since=None, until=None):
    """[{"pr", "sha", "end", "queued", "ran", "ok", "merge", "top",
    "main"}] from verdicts.log, for runs that ended in the window and
    say how long they ran."""
    runs = []
    for line in lines:
        got = VERDICT.match(line)
        if got:
            end = parse_time(got.group(1))
            ran = int(got.group(8)) if got.group(8) else None
            waited = int(got.group(7)) if got.group(7) else 0
            steps = got.group(6) or ""
            runs.append({
                "pr": int(got.group(3)), "sha": got.group(4), "end": end,
                "ran": ran, "top": bool(got.group(2)),
                "ok": got.group(5) == "green", "main": False,
                "merge": steps == "merge",
                "queued": end - datetime.timedelta(
                    seconds=(ran or 0) + waited)})
            continue
        got = MAIN.match(line)
        if got:
            end = parse_time(got.group(1))
            ran, waited = int(got.group(4)), int(got.group(3))
            runs.append({"pr": 0, "sha": got.group(2), "end": end,
                         "ran": ran, "top": False, "ok": True, "main": True,
                         "merge": False, "queued": end - datetime.timedelta(
                             seconds=ran + waited)})
    return [r for r in runs if (not since or r["end"] >= since)
            and (not until or r["end"] < until)]


def minute_of(start, when):
    return max(0, int((when - start).total_seconds() // 60))


def median(values, default=None):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else default


def extract(merger_lines, verdict_lines, since=None, until=None,
            urgent=(), slots=None):
    """The trace (see the module's docstring) of the window."""
    records = read_records(merger_lines, since, until)
    if not records:
        raise ValueError("no Merger records in the window")
    start = since or records[0][0]
    end = until or records[-1][0] + datetime.timedelta(minutes=1)
    minutes = minute_of(start, end)
    verdict_lines = list(verdict_lines)
    runs = read_runs(verdict_lines, start, end)
    stopped = []
    for line in verdict_lines:
        got = STOPPED.match(line)
        if got:
            when = parse_time(got.group(1))
            if start <= when < end:
                stopped.append(int(got.group(2)))
    inferred = []

    seen = collections.defaultdict(list)     # pr: [minute present]
    record_minutes = []
    base, mac, holds_ahead = {}, set(), set()
    merged, merges = {}, []
    with_main = []                           # (minute, pr)
    author_backs = collections.defaultdict(list)   # pr: [minute]
    for when, record in records:
        m = minute_of(start, when)
        record_minutes.append(m)
        present = set(record.get("queue") or ())
        for key, why in (record.get("held") or {}).items():
            try:
                number = int(key)
            except ValueError:
                continue
            present.add(number)
            why = str(why)
            got = STACKED.match(why)
            if got:
                base.setdefault(number, int(got.group(1)))
            got = AHEAD_OF.match(why)
            if got:
                holds_ahead.add(tuple(sorted((number, int(got.group(1))))))
            if GATED in why:
                mac.add(number)
        for number in present:
            if not seen[number] or seen[number][-1] != m:
                seen[number].append(m)
        for d in record.get("decisions") or ():
            number = d.get("pr")
            if not isinstance(number, int):
                continue
            reason = str(d.get("reason") or "")
            if d.get("action") == "MERGE" and d.get("result") == "done" \
                    and number not in merged:
                merged[number] = m
                merges.append([number, m])
            if d.get("action") == "WAIT" and GATED in reason:
                mac.add(number)
            if d.get("action") == "HAND-BACK":
                if "conflicts with main" in reason:
                    with_main.append((m, number))
                elif d.get("result") == "done":
                    author_backs[number].append(m)
    for r in runs:
        if r["merge"] and not r["top"]:
            with_main.append((minute_of(start, r["end"]), r["pr"]))

    traced = set(seen)
    first_minute = record_minutes[0]
    by_pr = collections.defaultdict(list)
    for r in runs:
        if not r["main"] and not r["top"]:
            by_pr[r["pr"]].append(r)

    # Each conflict with the default branch is put down to the pull
    # request merged last before it.
    pairs = set(holds_ahead)
    blamed = 0
    for m, number in sorted(with_main):
        before = [n for n, at in merges if at <= m and n != number]
        if before:
            pairs.add(tuple(sorted((number, before[-1]))))
            blamed += 1
    # A layer can't conflict with its own stack in a way the logs show:
    # the Merger restacks it. Pairs within one stack are dropped.
    def chain(number):
        seen_ = set()
        while number in base and number not in seen_:
            seen_.add(number)
            number = base[number]
            seen_.add(number)
        return seen_
    pairs = {(a, b) for a, b in pairs
             if b not in chain(a) and a not in chain(b)}
    if blamed:
        inferred.append(
            "%d conflicts with the default branch put down to the pull "
            "request merged last before each" % blamed)

    prs = {}
    early = 0
    gaps_total = 0
    for number in sorted(traced):
        minutes_seen = seen[number]
        approved = [minutes_seen[0]]
        pushes, gaps = [], []
        # A run of ticks without it, while it is still open: handed
        # back, then approved again.
        present = set(minutes_seen)
        last = minutes_seen[0]
        gap = False
        for rm in record_minutes:
            if rm <= minutes_seen[0]:
                continue
            if number in merged and rm >= merged[number]:
                break
            if rm in present:
                if gap:
                    approved.append(rm)
                    gaps[-1][1] = rm
                    gap = False
                last = rm
            elif not gap and rm - last >= 2:
                gap = True
                gaps.append([rm, None])
                gaps_total += 1
        if minutes_seen[0] == first_minute:
            early += 1
        own = sorted(by_pr.get(number, ()), key=lambda r: r["queued"])
        opened = approved[0]
        before = [r for r in own
                  if minute_of(start, r["queued"]) < approved[0]]
        if before:
            opened = minute_of(start, before[0]["queued"])
            shas = []
            for r in before:
                if r["sha"] not in shas:
                    shas.append(r["sha"])
                    if len(shas) > 1:
                        pushes.append(minute_of(start, r["queued"]))
        # A hand-back for anything but a conflict (its head isn't the
        # clean merge it says, a gate refused it): its author's next
        # head, then an approval of it, as the build check saw them.
        backs = []
        for m in author_backs.get(number, ()):
            if number in merged and m >= merged[number]:
                continue
            after = [r for r in own if minute_of(start, r["queued"]) > m]
            back = after[0] if after else None
            backs.append([m, minute_of(start, back["queued"]) if back
                          else None,
                          minute_of(start, back["end"]) if back else None])
        red = fixed = None
        decided = [r for r in own if not r["merge"]]
        if decided and not decided[0]["ok"]:
            red = minute_of(start, decided[0]["queued"])
            later = [r for r in decided[1:] if r["ok"]]
            if later:
                fixed = minute_of(start, later[0]["queued"])
        prs[str(number)] = {
            "opened": opened, "approved": approved,
            "pushes": sorted(set(pushes)), "gaps": gaps,
            "handed_back": backs,
            "base": base.get(number), "mac": number in mac,
            "urgent": number in set(urgent),
            "red": red, "fixed": fixed,
            "conflicts": sorted({b if a == number else a
                                 for a, b in pairs if number in (a, b)}),
            "run_minutes": median([r["ran"] / 60 for r in own
                                   if r["ran"]]),
            "merged": merged.get(number)}
    if early:
        inferred.append("%d pull requests already approved when the "
                        "window opened, taken as approved at minute 0"
                        % early)
    if gaps_total:
        inferred.append("%d times a pull request left the Merger's queue "
                        "and came back, taken as its author pushing and "
                        "its reviewer approving again" % gaps_total)
    backs = sum(len(p["handed_back"]) for p in prs.values())
    if backs:
        inferred.append("%d hand-backs for something other than a "
                        "conflict: the author's next head taken as pushed "
                        "when the build check queued it, and approved when "
                        "its run ended" % backs)
    if mac:
        inferred.append("the gated instance's runs take %d minutes (no "
                        "log of them is read)" % MAC_MINUTES)

    background = []
    for r in runs:
        if r["main"]:
            background.append({"at": minute_of(start, r["queued"]),
                               "minutes": round(r["ran"] / 60, 1),
                               "main": True})
        elif not r["top"] and r["pr"] not in traced and r["ran"]:
            background.append({"at": minute_of(start, r["queued"]),
                               "minutes": round(r["ran"] / 60, 1),
                               "main": False})
    background.sort(key=lambda b: b["at"])
    head = median([r["ran"] / 60 for r in runs
                   if not r["top"] and not r["main"] and r["ran"]], 20)
    top = median([r["ran"] / 60 for r in runs if r["top"] and r["ran"]],
                 head)
    resolve = median([b - a for a, b in _resolve_delays(prs)], 60)
    if not slots:
        inferred.append("the slot count isn't in the logs; give --slots")
    return {
        "version": 1, "start": iso(start), "minutes": minutes,
        "slots": slots, "prs": prs, "background": background,
        "run_minutes": {"head": head, "top": top},
        "mac_minutes": MAC_MINUTES,
        "resolve_minutes": resolve,
        "real": {
            "merges": merges,
            "head_runs": sum(1 for r in runs if not r["top"]
                             and not r["main"] and r["pr"] in traced),
            "top_runs": sum(1 for r in runs if r["top"]),
            "main_runs": sum(1 for r in runs if r["main"]),
            "stopped_runs": sum(1 for n in stopped if n in traced),
            "background_runs": sum(1 for b in background
                                   if not b["main"])},
        "inferred": inferred}


def _resolve_delays(prs):
    """(left, back) for each time a pull request left the queue and
    came back: how long an author takes to answer a hand-back."""
    for pr in prs.values():
        for left, back in pr["gaps"]:
            if back is not None and back > left:
                yield left, back


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--merger", required=True,
                        help="the Merger's merger.jsonl")
    parser.add_argument("--verdicts", required=True,
                        help="the build check's verdicts.log")
    parser.add_argument("--since", help="window start, ISO time")
    parser.add_argument("--until", help="window end, ISO time")
    parser.add_argument("--urgent", default="",
                        help="the urgent pull requests, N,N")
    parser.add_argument("--slots", type=int, help="the build check's slots")
    args = parser.parse_args(argv)
    since = parse_time(args.since) if args.since else None
    until = parse_time(args.until) if args.until else None
    urgent = [int(n) for n in args.urgent.split(",") if n.strip()]
    with open(args.merger) as m, open(args.verdicts) as v:
        trace = extract(m, v, since, until, urgent, args.slots)
    json.dump(trace, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
