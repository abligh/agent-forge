"""Quiet holds: a shared host asked to stay quiet for a while.

An agent timing something on a host other agents share asks the rest
to hold heavy work: builds, check runs, deploys. A **hold** is one file
in the project's quiet directory (`quiet.dir`), named for its holder,
whose first line is

    until <RFC 3339 time> <reason>

A hold is **live** until its time. One past its time is void, and so is
a file this can't read: a hold left behind by a holder that died, or
written by hand and got wrong, never blocks anything for longer than
it said. A holder has at most one hold; holding again replaces it.

Nothing here runs a command: the configuration names a directory, and
whatever honours a hold (a forge tool, or a project's own job through
`forge-quiet status`) reads it.
"""

import datetime
import os
import re

# The longest a hold may be taken for, unless `quiet.max_minutes` says.
DEFAULT_MAX_MINUTES = 120
# A holder's name, as its file's: an agent's name, or anything as plain.
HOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LINE_RE = re.compile(r"^until[ \t]+(\S+)(?:[ \t]+(.*?))?[ \t]*$")


def parse_time(text):
    """An RFC 3339 time ("2026-10-19T17:30:00Z", with or without
    seconds, any offset) as an aware datetime, or None."""
    text = text.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse(text):
    """(until, reason) from a hold file's text, or None if its first
    line isn't `until <time> [reason]`."""
    first = (text or "").splitlines()[0] if text else ""
    m = LINE_RE.match(first)
    if not m:
        return None
    until = parse_time(m.group(1))
    if until is None:
        return None
    return until, (m.group(2) or "").strip()


def stamp(when):
    return when.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def live(folder, now):
    """[(holder, until, reason)] for each hold in `folder` still live at
    `now`, soonest to end first. A folder that doesn't exist holds
    nothing."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    holds = []
    for name in names:
        if not HOLDER_RE.match(name):
            continue
        path = os.path.join(folder, name)
        try:
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                found = parse(handle.read(4096))
        except OSError:
            continue
        if found is not None and found[0] > now:
            holds.append((name, found[0], found[1]))
    return sorted(holds, key=lambda h: (h[1], h[0]))


def describe(holds):
    """One line per hold, for a log or a message."""
    return ["%s until %s%s" % (holder, stamp(until),
                               ": " + reason if reason else "")
            for holder, until, reason in holds]


def hold(folder, holder, minutes, reason, now, max_minutes=None):
    """Take (or replace) `holder`'s hold for `minutes`, written whole
    and renamed into place so a reader never sees half of it. Returns
    its end. ValueError for a bad holder, a time not above 0 or over
    the limit, or a reason with a line break."""
    limit = DEFAULT_MAX_MINUTES if max_minutes is None else max_minutes
    if not HOLDER_RE.match(holder or ""):
        raise ValueError("a holder is letters, digits, '.', '_' or '-': %r"
                         % holder)
    if not 0 < minutes <= limit:
        raise ValueError("a hold is for 1 to %d minutes, not %s"
                         % (limit, minutes))
    if "\n" in reason or "\r" in reason:
        raise ValueError("a reason is one line")
    until = now + datetime.timedelta(minutes=minutes)
    path = os.path.join(folder, holder)
    temp = os.path.join(folder, ".%s.%d" % (holder, os.getpid()))
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write("until %s %s\n" % (stamp(until), reason.strip()))
    os.chmod(temp, 0o664)
    os.replace(temp, path)
    return until


def release(folder, holder):
    """Drop `holder`'s hold. True if there was one."""
    if not HOLDER_RE.match(holder or ""):
        raise ValueError("a holder is letters, digits, '.', '_' or '-': %r"
                         % holder)
    try:
        os.unlink(os.path.join(folder, holder))
    except FileNotFoundError:
        return False
    return True
