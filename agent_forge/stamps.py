"""Correcting GitHub's future-stamped reviews and comments (#183).

GitHub has stamped records with times that hadn't come yet. On
2026-11-01 at 09:49:05Z by its own Date header, GraphQL gave two reviews
posted around 08:59Z the times 09:58:54Z and 09:59:06Z, though their ids
were below those of three later approvals. Anything that orders by stamp
then reads the older review as the newer one. The Merger held a pull
request for "a review after the approval", and the router handed it back
to its author, until someone approved after the bad stamp had passed.

Ids don't lie: a pull request's reviews are numbered in the order they
were started, and its comments in the order they were posted. So within
each sequence, no record can be later than the next one by id. `correct`
caps `createdAt` at that. It moves a review's `submittedAt` back by the
same amount, but never below the corrected `createdAt`.

It is not capped at the read time. A cap that moves every tick would
change every key built from a stamp (the Merger's hand-back and
held-comment keys use the approval's `submittedAt`), and the Merger
would post the same comment again on each tick. The id cap only moves
when a new record arrives. A future-stamped newest record stays as it
is until one does, or until GitHub's clock passes it.

Why `createdAt` and not ids alone: a review gets its id when it is
started, so a pending review's `submittedAt` can rightly come after a
newer review's. Ordering by id would get that case wrong. Review ids and
comment ids are separate sequences, so ids can't order one against the
other; corrected stamps can.

A record without `databaseId` or `createdAt` is left as it is, and so is
everything once GitHub's stamps are right again.
"""

import datetime

FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(stamp):
    return datetime.datetime.strptime(stamp, FORMAT).replace(
        tzinfo=datetime.timezone.utc)


def _iso(when):
    return when.astimezone(datetime.timezone.utc).strftime(FORMAT)


def _usable(record):
    if not isinstance(record, dict) or record.get("databaseId") is None:
        return False
    try:
        _parse(record.get("createdAt") or "")
    except ValueError:
        return False
    return True


def correct_sequence(records):
    """Correct `records` (one id sequence: a pull request's reviews, or
    its comments) in place. Returns how many records were moved."""
    moved = 0
    # Records sharing an id (only fixtures do) don't cap each other: each
    # is capped by the newest id above them, and caps those below. The
    # newest id has no cap.
    group_id, group_cap, next_cap = None, None, None
    for record in sorted((r for r in records if _usable(r)),
                         key=lambda r: r["databaseId"], reverse=True):
        if record["databaseId"] != group_id:
            group_id, group_cap = record["databaseId"], next_cap
        cap = group_cap
        created = _parse(record["createdAt"])
        if cap is not None and created > cap:
            delta = created - cap
            record["createdAt"] = _iso(cap)
            submitted = record.get("submittedAt")
            if submitted:
                try:
                    record["submittedAt"] = _iso(
                        max(_parse(submitted) - delta, cap))
                except ValueError:
                    pass
            created = cap
            moved += 1
        next_cap = created if next_cap is None else min(next_cap, created)
    return moved


def correct(node):
    """Correct `node`'s `reviews` and `comments` in place (a pull request
    or issue, as lists or as GraphQL connections) and return `node`."""
    for field in ("reviews", "comments"):
        records = node.get(field)
        if isinstance(records, dict):
            records = records.get("nodes")
        if isinstance(records, list):
            correct_sequence(records)
    return node
