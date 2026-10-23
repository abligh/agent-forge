# 0027: A path no reviewer covers raises an issue

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-23

## Summary

Each reviewer's `reviews` globs say which paths are hers. When a pull
request changes a path that matches no reviewer's globs, the router
today gives that path's lines to the first reviewer in the
configuration, and says nothing about it. That reviewer may then
decline the pull request as outside her area, and nobody learns that
the configuration has a gap.

With `router.unreviewed`, the router opens one issue for each such
gap, assigned to the triage owner, so that someone gives the path a
reviewer. It still asks the first reviewer meanwhile, so no pull
request waits on the issue.

## Evidence

On one project, a reviewer declined a test-only pull request because
its directory, `desktop/`, was in no reviewer's globs. The pull request
waited for an owner to notice. A check of that project's tree then
found eight more top-level areas with no reviewer, some of them in use
for weeks: a crate, the test corpora and the root build
files. The project's owner asked for an uncovered area to raise an
issue, not to go silently to a default reviewer.

A project can test that every tracked file on its default branch is
covered. That test can't see a pull request that adds a new top-level
path, because that path exists only on the pull request's branch.

## Design

`router.unreviewed` (default off). When set, on each sweep, for each
open pull request that isn't a draft:

1. **Find the uncovered paths.** These are the changed paths that match
   no reviewer's `reviews` glob, by the same `fnmatchcase` the reviewer
   choice uses. Paths under `router.unreviewed.ignore` are left out
   (for example the configuration directory a code owner reviews).
2. **Group them** by their top-level area: the first path segment, or
   the first two under a directory listed in `router.unreviewed.depth2`
   (for example `crates`).
3. **Open one issue per area** that has no open issue yet, titled
   `No reviewer covers <area>/ (first seen on #N)`. It lists the pull
   request and the paths, is labelled with `labels.owner_prefix` +
   the triage owner, and is assigned to the triage owner's account when
   the router has one. State: `unreviewed.json`, keyed by area, so a
   later pull request adds a line to the same issue, not a new issue.
4. **Say so once on the pull request**: a `Not-a-review` comment naming
   the issue, so its author and the reviewer it went to know.

The reviewer choice doesn't change: the uncovered lines still count
towards the first reviewer. A project that wants a named default
reviewer can still get one by giving her a `**` glob. This design
doesn't add another way to do that.

The issue closes by hand once the configuration covers the area. The
router comments on it, at most once a day, while open pull requests
still change uncovered paths there.

## Configuration

```json
"router": {
  "unreviewed": {"ignore": [".agent-forge/**"], "depth2": ["crates"]}
}
```

It needs `router.owners.triage`. Without a triage owner the setting is
refused at load.

## Not covered

- Choosing a reviewer for the area. That is the triage owner's call,
  through an ordinary configuration change.
- Paths a code owner reviews outside the reviewers' globs. List them
  under `ignore`.
