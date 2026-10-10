# 0003: Code owners in the merge queue

* Status: proposed
* Implemented: v0.4.9 (8e1026d), off until configured
* Author: XMPPEngineer
* Date: 2026-10-10

## Summary

A project made its owner's review of certain paths the decision that
turns a feature on: its CODEOWNERS file gives the configuration
directory, the host settings and the CODEOWNERS file itself to the
owner. The Merger never asked about it. It merges on a reviewer
agent's approval, and the repository's branch protection did not stop
its App. Two pull requests that changed owned paths merged in one
evening with no review from the owner. Until then, the owner had merged
every such pull request by hand, so nobody had noticed.

This proposes `merge.code_owners`: the Merger holds a pull request that
changes an owned path until an owner has approved its head.

## Goals

* **No owned path merges without its owner.** An owner's approval
  counts only when it covers the head.
* **Waiting is not failing.** The pull request is held, not handed
  back. Nothing is posted, and its author is asked for nothing.
* **It fails closed.** If the Merger can't tell which paths changed,
  or can't check an owner, the pull request waits.

## Non-goals

* **Teams and email owners.** The Merger can check only `@login`
  owners. A path a team owns waits until a person merges it by hand.
* **Replacing branch protection.** A project that can make GitHub
  enforce code-owner review for every App should also do that.

## Design

`merge.code_owners: true` (`forge-merger`). Each tick reads the default
branch's CODEOWNERS file (`.github/CODEOWNERS`, `CODEOWNERS` or
`docs/CODEOWNERS`, the first found). It reads it as GitHub does:
gitignore-style patterns, and the last matching line wins. For each
candidate, it lists the paths changed since the merge base with `main`.
For every path that has owners, one of them must either be the pull
request's author or have approved it. An approval counts when the
owner's newest review (approval, change request or dismissal) is
`APPROVED`, at a commit that covers the head under the rule a
reviewer's approval already follows.

Otherwise the pull request is held, and `held` reads
`waits for code owner @owner: path` (`and N more` when there are more).
A held pull request is neither updated, merged, handed back nor
predicted.

## Trade-offs and failure modes

* **An owner who is away holds up their paths.** That is the point:
  they own them.
* **A merge of `main` into the head keeps the owner's approval** if it
  is a clean merge, as it keeps a reviewer's. A change to the pull
  request's own content needs a fresh approval.
* **It costs one `git diff` per candidate per tick**, plus a coverage
  check per owner approval.

## Testing strategy

agent-forge's tests: pattern matching, last match wins, the hold,
approvals that do and don't cover the head, a later change request, an
owner's own pull request, team owners, paths that can't be listed, and
the setting off.

## Implementation plan

1. The key, off by default, in one release.
2. A project turns it on in its own configuration, in a pull request
   its owner merges.
