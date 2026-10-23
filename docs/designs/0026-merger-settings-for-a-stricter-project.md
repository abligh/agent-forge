# 0026: Merger settings for a stricter project

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-23

## Summary

A second project is moving from its own merge bot to `forge-merger`.
Most of what its bot does, `forge-merger` already does with
configuration. Nine behaviours have no setting yet. This design adds
one setting for each, every one off by default, so the projects already
on `forge-merger` see no change.

## Evidence

The project's bot and `forge-merger` were compared line by line, and
the project's tooling owner checked the comparison against the code.

Already the same, with configuration:
- approvals by reviewer Apps only;
- the code owner's hold (`merge.code_owners`);
- the coverage of the head by clean merges of `main`;
- the status and CI gate;
- pre-merge commands (`merge.gate`);
- hand-back wording and de-duplication;
- predictions;
- stall alerts;
- dry runs.

The project's state files have the same formats, so `forge-merger` can
run with `--state-dir` pointing at the old bot's directory.

The gaps below are behaviours the project relies on. Each was added to
its bot after a real incident, such as a merge past a blocker named in
a comment, or a queue stuck on a pull request whose CI never started.

## Design

Nine settings under `merge`, each off unless set:

1. **`resolve`**: `[{"paths": [...], "command": [...]}]`. When the only
   conflict with `main` is in the listed paths (generated files), the
   Merger does the following:
   - merges `main` in a scratch worktree;
   - runs `command` there, which regenerates those files;
   - commits as itself and pushes with a lease.

   That merge, and a later one whose only non-clean paths are the
   listed ones, still covers the head, once `command` with `--check`
   (a separate `check` argument list) passes on it. Any other conflict
   is handed back as today. One RESOLVE per approval and head.
2. **`hold_labels`**: a list of labels. A pull request carrying one is
   held, with the reason `labelled <name>`.
3. **`blocked_by`**: `"body"` (today) or `"everywhere"`. With
   `"everywhere"`:
   - the newest text with a `Blocked-by:` line decides, across the
     body, comments and reviews;
   - every strict `Blocked-by: #N` line in that text counts;
   - `Blocked-by: none` clears it;
   - a line that starts `Blocked-by:` but doesn't parse holds the pull
     request, because it was meant as a hold.
4. **`said_since`**: `{"author": true, "approvals": true, "quiet":
   [...]}`. `author: true` makes the author's own remarks after the
   approval hold too. `approvals: true` makes a later approval by
   another reviewer hold until the first reviewer approves again.
   `quiet` lists more logins whose remarks never hold (a router's, for
   example), beside the Merger's own.
5. **`update_once`**: `true` means no second `UPDATE` or `RESOLVE`
   after any hand-back under the same approval, not only after a
   conflict.
6. **`ci_never_started`**: minutes. No run of `merge.ci_workflow` is
   listed for the head, and the check runs show none starting. The
   Merger waits this long after the head's commit, then hands the pull
   request back ("CI never started; push a new head"), not STALLED.
   While check runs show the workflow starting, it waits.
7. **`queued_stall_minutes`**: while the build check's state shows the
   head queued and not yet running, the stall time is this, not
   `stall_minutes`. It reads the check's state through `--check-state`
   (the build check's state directory).
8. **Fetching a head by SHA.** When `refs/pull/N/head` can't be
   fetched, the Merger fetches the head commit by its SHA before it
   gives up. No setting: it's always on, and it only replaces a SKIP.
9. **`read_as`**: the App a dry run reads with, instead of the Merger's.

## What a project changes

A project sets only what it needs. The project moving now would set all
nine. It would run `forge-merger` side by side in `--dry-run` against a
copy of its old state, comparing each pull request's decision on every
tick, before it switches.

## Not covered

- **Close-and-reopen for a stale head.** The project's bot did this.
  `forge-merger`'s RESYNC and lost-head hand-back keep the queue
  moving, and the project has agreed to take those instead.
- **The project's router.** It reads the old bot's code for "is it the
  Merger's turn". It moves separately.
