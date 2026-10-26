# 0033: A stack layer waits for its approval

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-26

## Summary

With `merge.stacks` (designs/0025), when the Merger merges a pull
request, the layers stacked on it become the stack's next layers. The
Merger looks at each one once, on the next tick. A layer that is
eligible but for its base is retargeted and merged. A layer that isn't
eligible at that moment, usually because its reviewer approval hasn't
been posted yet, ends the stack for good. From then on the Merger only
holds it as `based on <branch>, which no open pull request heads`. Its
author has to notice, retarget it to `main` by hand and ask for its
approvals again.

This design keeps such a layer in the stack, waiting. Every tick the
Merger judges it again. Once it is eligible but for its base, the
Merger retargets it as it would have done on the first tick. Nobody has
to retarget it by hand.

Off unless `merge.stacks.wait_hours` is set.

## Evidence

One project, 23 to 26 October, `merge.stacks` on:

| | Layers |
| --- | --- |
| Retargeted by the Merger, on the tick after the layer below merged | 29 |
| Dropped from their stack on that tick | 33 |

* **More layers dropped out than stayed.** Of the 33, 31 had no reviewer
  approval at that moment. The other 2 had a `Blocked-by:` that the
  merge had just resolved, and GitHub hadn't caught up yet.
* **The approvals were usually on their way.** The project's reviewer
  agents post an approval once the head's build check is green. An
  upper layer's check is often red only because its head conflicts
  with `main` until it is restacked. So its approval waits for exactly
  the merge that then drops it.
* **A dropped layer took longer to merge.** From the layer below
  merging to the layer itself merging, the 27 dropped layers that have
  merged since took a median of 47 minutes (75th percentile 112, the
  longest 6.8 hours). The 26 layers retargeted on time took a median
  of 22 minutes (the longest 2.6 hours). Six dropped layers were still
  open when this was written.
* **Every dropped layer cost a round trip through people.** In the
  last evening alone, authors retargeted four dropped layers by hand,
  and agents posted at least six requests asking a reviewer to approve
  a layer before the one below merged.

## The change

`merge.stacks.wait_hours: N` (default none: as today).

1. **A layer that isn't ready waits.** When the layer below has merged
   and a layer is not eligible but for its base, the Merger no longer
   drops it from `stacks.json`. It stays there, marked `waiting` with
   the time it started waiting. The tick's `held` reason names both
   the merge and what is missing: `stacked on #Q, which merged; waiting
   for: no Reviewer approval`.
2. **It is judged again every tick.** On the first tick where it is
   eligible but for its base, the Merger retargets it to `main`
   (`RETARGET`, as today), and the stack goes on from there.
3. **Its place in the queue.** A layer that waited takes its usual
   place in the queue once it is retargeted, not the first place an
   unbroken stack has. It became ready later than the candidates
   ahead of it, so it shouldn't overtake them. The layers above it are
   still a stack: once it merges, they go first as today.
4. **The merged heads are kept.** While any layer waits, `stacks.json`
   keeps the merged heads of the layers below it (`heads`). The Merger
   needs them to restack a waiting layer, and to see that a clean merge
   of the layer below's head into it still leaves its approval
   covering it. Today the file expires after 24 hours (`STACK_FOR`).
   Each waiting layer now counts its own N hours instead, from when it
   started waiting.
5. **Waiting ends.** A layer stops waiting when it is retargeted, when
   it is closed, when its author moves its base, or after N hours. In
   the last case the Merger drops it as it does today, and says so once
   on the pull request:

   > This layer waited N hours for its approval after #Q merged, and
   > the Merger no longer retargets it. Retarget it to `main` and ask
   > for its approvals again.

   That comment carries the same `Not-a-review` marker as the Merger's
   other notes.

A waiting layer never holds anything else up. It is not a candidate,
so the queue goes on past it as it does for any held pull request.

## What it doesn't change

* **Eligibility.** A waiting layer needs exactly what a layer needs
  today: a reviewer approval that covers its head, nothing said since,
  no blocker or deferral. This design only stops the Merger from
  giving up on it.
* **Layers that are ready.** A layer that is eligible on the first tick
  is retargeted then, and takes the first place, as today.
* **The diff a reviewer sees.** A waiting layer stays based on the
  merged layer's branch, so its diff on GitHub is still only its own
  change.

## Alternatives

* **Retarget every layer above at once**, ready or not, as GitHub does
  when a merged branch is deleted. The layers would then wait in the
  queue like any other pull request. But a layer's diff against `main`
  then shows the merged layer's commits again until `main` is merged
  into it, so reviewers would be reviewing the wrong diff. The Merger
  would also lose track of the merged head it needs to restack the
  layer. Rejected.
* **Ask reviewers to approve upper layers before their checks are
  green.** That is a rule for each project's reviewers, and it works
  only as long as every reviewer remembers it. The evidence shows the
  requests it already takes. This design removes the timing from the
  question.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `merge.stacks.wait_hours` | none | How long a layer that wasn't ready when the layer below merged stays in its stack, judged every tick and retargeted once it's ready. None keeps today's behaviour: such a layer leaves the stack at once. 1 to 168. |

## Order of work

1. `forge-merger`: waiting layers in `stacks.json`, the `held` reason,
   the retarget, the expiry and its note; tests. `docs/config.md`'s
   `merge.stacks`. A release.

A project then sets `wait_hours` in its own configuration.
