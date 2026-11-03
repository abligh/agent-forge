# 0042: Predict a layer as it will be restacked

* Status: approved (Alex Bligh, 2026-11-03, in the terminal: "agent-forge #191 and #193 approved"; https://github.com/abligh/agent-forge/issues/191)
* Author: XMPPEngineer
* Date: 2026-11-02

## Summary

When the layer below a stacked pull request merges (designs/0025), the
layer still carries the lower layer's own commits, which `main` now
holds only as a squash. Merged plainly with `main`, such a layer
conflicts, although the Merger will restack it cleanly when it reaches
it (`RESTACK`, then `STACK-UPDATE` through `based_merge`).
`predict_ahead` merges plainly, so it sees a conflict. If the layer is
overdue (designs/0037), the list ends there. If not, the layer is left
out, and so are the predictions that would have contained it.

On 2026-11-02, from about 19:00 to 21:10Z, every recorded tick of one
project's Merger predicted only the queue's first pull request. Four
approved layers whose lower layers had merged sat in a queue of 24,
and the first of them was overdue. So the
Merger merged one pull request per full check, at about 40 minutes
each: 4 merges in an hour, against 15–20 when batches run. The
authors restacked the layers by hand to unblock it.

This design predicts such a layer with the trees the Merger itself will
make when it restacks it. Off by default.

## Design

### The prediction

With `merge.stacks.predict_restacked: true`, `predict_ahead` treats a
pull request in the stack's `pending` list, whose entry records the
lower layer's final head `L`, differently. On `S(k-1)`, the commit
predicted before it, its tree is what the Merger's own steps would give:

1. If the head `H` doesn't contain `L` (the Merger's `RESTACK`), `H'` is
   `git merge-tree --write-tree H L`, committed with `H` and `L` as
   parents and the predictions' fixed identity and date. If git
   conflicts, so would `RESTACK`, which hands the layer back. The layer
   is treated as a conflict, as today.
2. Its tree is `based_merge(S(k-1), H', L)`, the same call `decide`
   makes for `STACK-UPDATE`, with the same safety conditions (`H'`
   holds `L`, and `S(k-1)`'s first-parent history holds `L`'s squash).
   `None` is a conflict, as today.

The entry is committed on `S(k-1)` like any other and listed in
`ahead.json` with `"restacked": true`. Every other pull request is
predicted exactly as today.

### Why it matches

The build check matches by tree. `STACK-UPDATE` pushes a commit whose
tree is `based_merge(main, H', L)`. Once the pull requests predicted
ahead of the layer have merged, `main`'s tip has `S(k-1)`'s tree, so
the pushed head has the predicted tree and is posted green at once
(`MATCHED`). Batches (designs/0032) then merge it with the rest. If any
input differs, the trees differ, nothing matches, and the layer is
checked in full as today. A wrong prediction costs a slot, never a
wrong merge.

### Early hand-backs

`early_conflicts` leaves layers out because the Merger restacks them.
With this option, it hands back early a layer for which step 1 or 2
conflicts on `main` itself. That's a real conflict, which `RESTACK` or
`STACK-UPDATE` would hand back when the layer reaches the front. It
uses the same reason (`RESTACK_REASON`), said once per approval and
head as today.

### Configuration

`merge.stacks.predict_restacked` (default `false`). It needs
`merge.stacks` and `merge.test_ahead`, and the configuration check
refuses it without them.

## Trade-offs

* **More git per tick.** That's two `merge-tree` calls and a
  `commit-tree` per pending layer, in a list of at most `depth` + 1.
  It's a few milliseconds each.
* **A restack the Merger never makes.** If the author restacks by hand
  first, the head changes and the layer leaves `pending` as today. The
  prediction is simply unused.
* **Overdue layers still end the list when they truly conflict.** That's
  designs/0037's rule and stays. Only the false conflict goes.

## Non-goals

* Restacking layers before they reach the front. That would push to
  authors' branches early and move their heads under them.
* Changing designs/0037's overdue rule.
* Pull requests stacked only in their description, which aren't
  `pending` layers.
