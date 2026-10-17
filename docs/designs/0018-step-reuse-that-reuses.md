# 0018: Step reuse that reuses

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-17

## Summary

Design 0009 (`checks.reuse`) skips a step whose inputs a green run here
already tested. On one project that has had it on since 10-11, it has
never reused a step: 0 of 3687 verdicts. Three things stop it, and any
one of them is enough.

1. **The default branch is never traced, so the records go stale.**
   With `checks.main: "every"`, a default-branch head whose tree a
   green run already passed is matched (`MAIN-GREEN … matched`), not
   run. That is nearly every head, since the Merger's prediction tested
   it. The project's last traced default-branch run was on 10-13.
   `every` also turns off `checks.fresh`, which would have asked for one
   (`fresh = … and not MAIN_EVERY`). Pull-request runs record only the
   steps marked `"reads_base": false`, so the four steps that read the
   base dropped out of the records one by one. Every later step reads
   at least one path the tree lacks, so it chains to them (cause 2).
   Since 10-16, no step of any verdict has had a digest.
2. **A path the tree lacks counts as written by an earlier step.**
   `step_digests` treats any read path missing from the tested tree as
   another step's output, and adds every earlier step's digest. Most such
   paths are lookups that found nothing, which 0009's item 3 means to
   digest as "absent". Only item 4, real outputs, should chain. On that
   project:
   * the formatter step read 486 such paths out of 856;
   * the lint step read 2144 out of 2662;
   * the web tests read 1232 out of 1524.

   The chain reaches the first step, which reads the base, so every
   step's digest changed whenever the default branch moved. Of 756
   digests recorded 10-11 to 10-14, 754 were unique for every step
   except the first.
3. **A directory read is digested by its tree id.** A step that opens
   a directory, such as `docs`, gets that directory's git tree id, and
   the tree id changes when anything beneath it changes. Item 2 of
   design 0009 digests a listed directory by its entry names. Across five
   default-branch commits, a change to one design document altered
   exactly one path in the reads of four steps: the `designs` directory
   itself.

Once those three are fixed, the project expects a head that changes
only documentation to need about 8 to 10 s of steps, after its own
test-time work. Most of what's left is the lint and formatter steps,
which re-run because a directory's tree id changed although no file
they read did. Reuse is how the check gets to 10 s.

## Design

Each part has its own switch, off by default, because each changes
which steps run.

### A. Trace a matched default-branch head when the records need it

With `checks.main: "every"` and `checks.depends`, a default-branch head
that would be matched first asks `main_needs` which steps lack a record
or have a stale one at its tree (the question `checks.fresh` already
asks). If any do, the head runs traced instead of being matched, at
most once every `depends_minutes` per slot.
`checks.reuse.trace_matched_main: true` turns this on.

Cost: one full traced run of the default branch when the records are
stale, about one every half hour on a busy day. That is what `needed`
would have cost.

### B. Let a pull request's run record a base-reading step

Not proposed. A step that reads the base reads the default branch's
tree, and a pull request's head merged with it is not the default
branch, so its record would mix the two. A keeps the records fresh
without this.

### C. Only paths an earlier step wrote chain

The tracer records, for each step, the paths it created or wrote (the
write set), alongside what it read. A read path that is missing from
the tested tree:

* is an output if it is in an earlier step's write set: the step's
  digest includes that step's digest (0009 item 4);
* otherwise is "absent", as 0009 item 3 meant, and nothing chains.

A project can also declare outputs a step leaves behind outside the
tracer's view, such as `node_modules/`, with a step's `"outputs"`
list. Until the records hold write sets (the next traced run of each
step), a step whose record has none chains as now.
`checks.reuse.chain_writes_only: true` turns this on.

### D. A directory is digested by its entry names

A read of a directory adds its sorted entry names (0009 item 2), not its
tree id. Files beneath it that the step read are in its read set and
digested one by one, so the tree id adds nothing but the changes the
step never looked at.

The tree id has been a safety net for a read the tracer missed. So D
keeps it for a directory that is in the read set while no file under
it is: there the tree id is the only evidence of what was read. Files
the tracer missed anywhere else are 0009's existing risk, bounded by
`max_age_days` and by default-branch runs never reusing.
`checks.reuse.dirs_by_names: true` turns this on.

### Recommended

All three, A, C and D, on together, after one traced default-branch run
to write fresh records with write sets. A alone fixes nothing: the
records come back, and every digest still chains to the base (C) and
changes with any directory (D).

## Config shape

```json
"checks": {
  "reuse": {"max_age_days": 7, "trace_matched_main": true,
            "chain_writes_only": true, "dirs_by_names": true},
  "steps": [{"name": "web dependencies", "run": ["npm", "ci"],
             "outputs": ["web/node_modules"]}]
}
```

## Expected effect

On the project above, after its own changes, a head that changes only
documentation or designs runs:

* the lint and formatter steps, reused unless a file they read changed;
* the tool tests (about 4 s), reused unless they read the changed file;
* the desktop step (about 1 s), which still runs while it reads the
  base.

That is about 2 s of steps plus the check's own 2 s, against 8 to 10 s
without reuse. A head that changes a crate gains little: its expensive
steps read what it changed.

## Trade-offs

* **C trusts the tracer's write set.** A step that writes through a
  path the tracer can't see would have its output digested as absent,
  and a later step could be reused on a stale input. `outputs` is the
  remedy, and a default-branch run never reuses, so the next one finds
  it.
* **D narrows what a directory read covers.** Keeping the tree id for
  a directory with no file read beneath it limits that to directories
  the step only listed, which is 0009's intent.
* **A spends a slot.** It runs only when a record is stale or missing,
  and rate-limited.

## Non-goals

* Reuse across hosts, or of red verdicts (as 0009).
* Changing which steps a head selects (0005).

## Implementation plan

1. The tracer records write sets, and records keep them (no behaviour
   change).
2. A, C and D behind their switches, with tests. Each test shows a
   case 0009 meant to reuse and now does, and a case that must still
   run: a real output changed, a file read under a directory changed,
   a stale record.
3. A project turns all three on after one traced default-branch run
   with step 1 installed, and reports its reuse rate a day later.
