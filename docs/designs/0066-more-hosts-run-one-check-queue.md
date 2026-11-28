# 0066: More hosts run one check queue

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-28

## Summary

A project's build check runs on one host. `checks.slots` sets how many
heads that host runs at a time, and nothing else can add capacity. When
the queue outgrows the host, heads wait. On one day, about 100 heads
waited behind a host whose load stayed near 100 all day, and the
project had no second host that could take any of them.

`checks.instances` (designs/0021) does not help here. An instance has
its own context and its own queue. It is a second check, not a second
pair of hands on the first one.

This design lets **helper hosts** run heads from the first host's
queue and post the same context:

1. **The home host stays as it is.** It runs the Merger and its own
   forge-pr-check. It keeps the queue, its order, every prediction,
   main and warm run, and the fast lane.
2. **It publishes its queue** to a ref, `refs/forge/check/<home>`. A
   helper reads that ref and takes heads only from it, in its order.
   A helper never builds a queue of its own.
3. **A head is claimed with a pending status** in the build context,
   with the description `running on <host>, lease <minutes>`. GitHub
   numbers statuses in the order they arrive, so the live claim with
   the lowest status id wins, and every other claimer stands down. The
   home host claims its own starts in the same way.
4. **A lease is timed by GitHub's clock** (`created_at`, and the `Date`
   header), never by a host's own. A run renews its lease every
   `renew_minutes`. When a lease lapses, its head is free to claim
   again.
5. **A helper posts its verdict itself,** as the poster App, exactly as
   the home host does. It writes the verdict's record (tree, steps,
   toolchain, failed steps' tails) to its own ref,
   `refs/forge/check/<helper>`. The home host reads those records into
   its state, so the Merger, stall detection and red hand-backs see
   them as they see their own.
6. **Each host keeps its own caches, slots and `cache_limit`.** Hosts
   join and leave through config: a `checks.helpers` block and a cron
   line on the new host.

Without `checks.helpers`, nothing changes.

## Evidence

- **One host is the ceiling.** forge-pr-check's queue, slots and
  verdicts live in one state directory (`state.json`, the `worker-N.lock`
  files). The Merger reads that same directory (`load_check_state`):
  `results`, `running`, `waiting` and `ahead_started`.
- **The status is already the shared fact.** The Merger gates a head
  on the build context's status (`status_path`, paginated) from the
  poster App, not on the check's files. A helper posting as that App
  is indistinguishable at the gate.
- **Verdicts are trusted only from their own host.** `tested` and
  `foreign` refuse every record whose `host` isn't
  `socket.gethostname()`, because the toolchain may differ. That rule
  stops a helper's green being reused for a tree the home host is
  asked about.
- **A hand-back quotes local logs.** The Merger reads the failed steps'
  tails from `logs/<sha>/<step>.log.gz` in the check's state directory
  (RED_TAIL_*). A helper's logs are on another host.
- **Capacity per unit of cost.** A smaller, cheaper class of host can
  give more cores per unit of cost, with less disk. A design that keeps each
  host's caches local, with its own `cache_limit`, can use that class
  of host.

## Design

### Config

```json
"checks": {
  "context": "ci/build-and-test",
  "slots": 6,
  "helpers": {
    "lease_minutes": 20,
    "renew_minutes": 8,
    "toolchain": [["rustc", "-vV"], ["cargo", "-V"]],
    "hosts": {
      "build-2": {"slots": 4, "cache_limit": "300G"},
      "build-3": {"slots": 4, "drain": true}
    }
  }
}
```

- **`hosts`** names the helper hosts, keyed by hostname. Each block may
  set `slots`, `caches`, `cache_limit` and `memory_cap`, the same keys
  as `checks`. A key the block leaves out comes from `checks`. Steps,
  context and poster always come from `checks`: a helper runs the same
  check.
- **`drain`** stops a helper from making new claims. Its running heads
  finish and post. Removing the block entirely does the same, and from
  then on the home host ignores that host's claims and records.
- **`toolchain`** lists commands whose output is hashed into a
  toolchain digest, which every record carries. Reuse of a record from
  another host (below) needs equal digests. Without `toolchain`, no
  record from another host is reused.
- **The home host** is the top-level `host`, as now.

forge-pr-check works out its role from its hostname: home, a helper
listed in `hosts`, or neither (it exits with a message). Config check
refuses a helper hostname equal to `host`, `lease_minutes` not greater
than `renew_minutes`, and a helper without App credentials on its
host. That last check happens at run time, as `POST-FAILED`.

### The published queue

On every tick that changes the queue, the home host writes
`refs/forge/check/<home>`: one commit with no parent, holding
`queue.json`. The file lists the waiting heads in `next_head`'s order,
each as `{pr, sha, since, urgent}`, plus `published` (GitHub's time of
the last write). It never lists a prediction, a main or warm run, or a
head held for review (designs/0064). The ref is moved with
`force: true`. Only the home host writes it, so nothing is lost, and
the ref never builds up history.

A helper skips the queue if `published` is older than
`2 × lease_minutes` (logs `QUEUE-STALE`). A home host that is down
then doesn't leave helpers working from an old list.

### Claiming

A host that wants to start a head (helpers take the first free head in
`queue.json`; the home host takes its usual choice):

1. **Posts** pending in the build context: `running on <host>, lease
   <lease_minutes>`.
2. **Reads** the head's statuses in that context, after
   `CLAIM_SETTLE` (5 s), to give GitHub's replicas time to catch up.
3. **Compares the live claims.** A live claim says `running on <h>`,
   with `h` the home host or a listed helper not draining. It is newer
   than `lease_minutes` by GitHub's `created_at`, compared against the
   `Date` of that read. The host also discards any claim from a host
   that has since posted a final status on the head.
4. **Starts, or stands down.** If its own claim has the lowest id among
   the live claims, it starts. Otherwise it logs `CLAIM-LOST #<pr>
   <sha> to <h>` and tries the next head.

While a run lasts, its tick posts the same pending again once the
newest claim is `renew_minutes` old. Each renewal is checked as in
step 3. A host whose claim is no longer the lowest live one (another
host saw it lapse during a stall) stops its run (`STOPPED-CLAIM`), as a
superseded run is stopped. Two runs of one head can then last at most
one tick.

A host posts no other pending status (`waiting`, `queued`) on a head
that has a live claim, so the status GitHub shows says where the head
runs.

**Status budget.** GitHub keeps at most 1,000 statuses per sha and
context. A three-hour run at `renew_minutes: 8` posts 23. Readers
already paginate.

### Verdicts and records

A helper posts its verdict as the home host does (`post_verdict`,
tiers included). It then adds the run's record to `records.json` on
its own ref, `refs/forge/check/<helper>`. The record is today's
`results` entry plus `host`, `toolchain` and, for each failed step, its
last RED_TAIL_LINES lines. Only verdicts newer than KEEP are kept. A
failed write is retried on the next tick (`RECORD-PENDING`). Until it
goes through, the verdict still gates, because the status is what the
Merger reads.

On every tick, the home host reads each helper's ref and merges new
records into `results`, after two checks:

- **The record must agree with the poster App's final status** on that
  sha in the build context.
- **Its `tree` must equal the sha's tree** in the home host's object
  store. The sha is fetched if it is missing.

A record that fails either check is logged `RECORD-REFUSED` and
dropped. The ref is writable by anyone who can push, so these checks
leave a forged record able to lie only about its steps or toolchain
digest, and only for a sha whose real status it matches.

### What trusts what

- **The head's own verdict gates as posted,** from whichever host. This
  is the point of the design.
- **Reuse for another sha** (`tested`: predictions, batches, an
  `UPDATED` head) takes a record from another host only when its
  `steps` and `toolchain` digests both equal the home host's.
  `foreign` gets the same rule. Without `toolchain`, the rule is
  today's: own host only.
- **`--relabel-host` is unchanged.** It is for moving a host.

### What the home host and the Merger see

`load_check_state` (Merger) gains helpers' records in `results` and
their live claims in `running`, as `{pr, sha, started, host}`. Stall
detection (`stalled_for`, `stall_limit`), `head_red` and
`batch_top_started` read those fields as they are. The red hand-back
takes a helper's tails from the record instead of `logs/`. The Merger
itself needs no other change.

The home host's tick stops a helper run only through the claim:
supersession is still each host's own business. A helper's tick stops
its runs of heads no open pull request has, with the existing code.

### Failure

- **A helper dies:** its leases lapse within `lease_minutes`, and its
  heads go back to the front of their place in the queue.
- **The home host dies:** the published queue goes stale, and helpers
  stop taking heads after `2 × lease_minutes`. Their running heads
  finish and post. Nothing merges anyway while the Merger is down.
- **GitHub is unreachable** from one host: it can't renew, so its claim
  lapses, and its run may still post a verdict later. A late verdict
  on a head another host has since claimed is still posted. The newer
  run's verdict follows it, and the Merger reads the newest. Both
  records are kept, as two runs on one host would be.
- **Clock skew:** leases and `published` use GitHub's times only. A
  record's `finished` (MATCH_MAX_AGE, KEEP) is the host's own time. A
  helper whose clock differs from GitHub's `Date` by more than 60 s
  claims nothing and logs `CLOCK-SKEW`.
- **A head moves mid-run:** the existing `SUPERSEDED` path, on the host
  running it.

## Steps

1. Toolchain digest in every record, and the reuse rule. This is inert
   without `checks.helpers`.
2. Claims and leases on the home host alone. Its own starts claim and
   renew, so the protocol runs in production with one host before a
   second joins.
3. The published queue, helper mode, helper records and the home
   host's merge of them. This includes `load_check_state` and the
   red-tail source.
4. Config check, `drain`, and the documentation in forge-pr-check's
   docstring.

## Not in this design

- **Predictions on helpers.** A train's top or a red batch's search
  runs on the home host. That is where the Merger's `ahead.json` and
  most of its trust logic live. Once a toolchain digest matches,
  moving them would be a later design, and designs/0057's published
  train tops would be its starting point.
- **Cache affinity.** A head goes to whichever host claims it first.
  Whether to prefer the host that last ran its pull request (warm
  incremental state) is a question for after step 3, with numbers.
- **Instances on helpers.** An instance (designs/0021) stays one host.

## Open questions

1. **Ref writes.** `refs/forge/check/*` needs `contents: write` for the
   poster App, and a forced update of one ref per host. Is a forced
   update acceptable outside protected branches, or should each write
   be a normal commit on a branch that is pruned now and then?
2. **Lease length.** 20 minutes bounds the delay after a host death.
   Shorter leases mean more statuses and more API calls.
3. **Toolchain digest.** Is a command list enough, or should a
   container image digest be part of it?
4. **Where helpers sit in the queue.** As written, a helper takes the
   first unclaimed head, so the home host and the helpers share one
   order. Should urgent heads and the Merger's first pull request stay
   on the home host, where a fast lane and warm caches are?
