"""The lines agents write that tools act on.

* `Not-a-review` opening a comment or review: it asks for something
  rather than judging, so it neither counts as speaking after an
  approval nor wakes anyone as a review would.
* `Blocked-by: #N` on a line of its own in a pull request's body: hold
  it until N closes.
* `Defer-until: <time>`: hold it until then. The newest such line wins.

Only prose counts: a marker quoted in a fenced or indented code block
is not one, so a comment can explain the markers without setting them.
"""

import datetime
import re

NOT_A_REVIEW_RE = re.compile(r"^Not-a-review\b", re.MULTILINE | re.IGNORECASE)
BLOCKED_RE = re.compile(r"^\**Blocked-by:\**\s*#(\d+)\s*\**$",
                        re.MULTILINE | re.IGNORECASE)
FENCE_RE = re.compile(r"^\s*(```|~~~).*?(^\s*\1|\Z)", re.MULTILINE | re.DOTALL)
DEFER_RE = re.compile(r"^Defer-until:[ \t]*(.+?)[ \t]*$",
                      re.MULTILINE | re.IGNORECASE)
DEFER_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d")


def prose(text):
    """`text` without fenced or indented code."""
    text = FENCE_RE.sub("", text or "")
    return "\n".join(line for line in text.splitlines()
                     if not line.startswith(("    ", "\t")))


def is_not_a_review(text):
    return bool(NOT_A_REVIEW_RE.search(prose(text)))


def blockers(text):
    """The issue and pull request numbers `text` names in `Blocked-by:`
    lines."""
    return {int(m.group(1)) for m in BLOCKED_RE.finditer(prose(text))}


def parse_time(value):
    """A `Defer-until:` value as an aware datetime, or None if it is not
    one of DEFER_FORMATS. A value without a zone is UTC."""
    for fmt in DEFER_FORMATS:
        try:
            when = datetime.datetime.strptime(value.replace("Z", "+0000"), fmt)
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(
            tzinfo=datetime.timezone.utc)
    return None


def latest_deferral(texts):
    """The newest `Defer-until:` value across `texts`, a list of (time,
    text) where time sorts as written (ISO 8601), or None."""
    latest = None
    for _, text in sorted(texts, key=lambda t: t[0] or ""):
        found = DEFER_RE.findall(prose(text))
        if found:
            latest = found[-1]
    return latest


def deferred(texts, now):
    """Why something is deferred at `now`, or None. An unexpired
    deferral holds it, and so does one that isn't a time: a deferral
    nobody can read is not a release."""
    latest = latest_deferral(texts)
    if latest is None:
        return None
    until = parse_time(latest)
    if until is None:
        return "Defer-until: %s is not a time" % latest[:40]
    if until > now:
        return "deferred until %s" % until.astimezone(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None
