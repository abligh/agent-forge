# forge-closing-keywords

`forge-closing-keywords` reports every issue a pull request's squash
merge will close, and refuses one the pull request doesn't declare or
one closed by a sentence that denies it. A closing keyword (`fixes #12`)
in a commit message body closes the issue on a squash merge, but
GitHub's `closingIssuesReferences` field reads only the pull request
body. So what a tool can query and what the merge does disagree. This
reads both. Once a keyword is in a merged commit message nobody can take
it out, so the check has to run before the merge, as a Merger gate.

## Usage

    forge-closing-keywords [RANGE]
    forge-closing-keywords --pr N [--include-subject] [--below M...] [RANGE]
    forge-closing-keywords --check-body FILE --surface body|commit [--declares N...]

**As a Merger gate**, in `merge.gate`:

```json
"merge": {"gate": [["forge-closing-keywords", "--pr", "{pr}", "--include-subject"],
                   ["forge-commit-identity", "{base}..{head}"]]}
```

A project that merges stacks of pull requests (`merge.stacks`) adds the
merged layer below, which the Merger fills in or leaves out:

```json
["forge-closing-keywords", "--pr", "{pr}", "--include-subject",
 "--below", "{below}"]
```

The Merger runs gates in the cron checkout with `GH_TOKEN` set. Exit 3
is a refusal for now: the Merger says it once and asks again about five
minutes later.

**By an agent**, before pushing or posting:

```sh
forge-closing-keywords --pr 34                   # the open pull request
forge-closing-keywords                           # origin/main..HEAD
forge-closing-keywords --check-body body.md --surface body --declares 12
forge-closing-keywords --check-body msg.txt --surface commit
```

**In CI**, as a step that runs `--pr` on the pull request being built.
Set [`closing_keywords.enforced_by` and `rerun`](../config.md#keys) so
its messages name the workflow and how to rerun it.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `RANGE` | `origin/<default_branch>..HEAD`; with `--pr`, the pull request's own commits | Commits to judge, as `git log` reads them. With `--pr`, a range is for judging a commit not pushed yet: it must contain every one of the pull request's commits, and nothing already on its base branch. |
| `--pr N` | none | The pull request this is about: its commits (read from GitHub), its declaration (`closingIssuesReferences`), its title and its body. |
| `--include-subject` | off | Also judge commit subject lines. Without it, a subject keyword is reported, not refused. Without `--pr`, refused on a branch that has an open pull request. |
| `--below M...` | none | One or more merged pull requests: stack layers below this one. Their commits are left out. Needs `--pr`. A number that hasn't merged is exit 2. |
| `--check-body FILE` | none | Check a text not on GitHub yet: a pull request body, a comment, or a commit message about to be used with `git commit -F`. Needs `--surface`. |
| `--surface body\|commit` | none | Where the `--check-body` text is going. Only with `--check-body`. A code span disarms a keyword on `body`, but not on `commit`. |
| `--declares N...` | none | With `--check-body`: the issues the text means to close. Only keywords naming other issues are findings. Zero numbers means "declares nothing". Only with `--check-body`. |
| `-h`, `--help` | | Print the usage and exit. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_AGENT` | If set, and its App's token mints, GitHub is read as that App. |
| `GH_TOKEN` | Else this token is used for reading (the Merger sets it for its gates). Else it reads anonymously. |
| `FORGE_CONFIG` | The configuration file itself. |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when `FORGE_CONFIG` is not set. |
| `FORGE_GITHUB_API` | The GitHub API base URL. Default `https://api.github.com`. |

## Configuration

A configuration is required (exit 2 without one). It reads:

* [`repo`](../config.md#keys): the repository whose issues a keyword
  can close. A reference to another repository is ignored;
* [`default_branch`](../config.md#keys): the start of the default range;
* [`closing_keywords`](../config.md#keys): `enforced_by`, what refuses a
  pull request this check fails (default `the Merger`), and `rerun`, how
  to run the check again (default `run the check again (the Merger
  does, within a few minutes)`). They only change the wording of its
  messages.

## What it does

**A closing keyword** is `close`, `closes`, `closed`, `fix`, `fixes`,
`fixed`, `resolve`, `resolves` or `resolved`, in any case, with an
optional colon, then whitespace, then one of: `#N`, `[#N` (a Markdown
link), `example/widget#N` (the configured `repo`), or
`https://github.com/example/widget/issues/N`. As measured on GitHub,
`close (#N)`, `close *#N*` and `close "#N"` close nothing and aren't
flagged; nor is a `/pull/N` URL. A keyword naming a pull request, or
an issue already closed, is never flagged, since closing it does
nothing. A number that can't be looked up is exit 2.

**GitHub ignores negation.** "This does not close #12." closes #12. So
a "not" never excuses a keyword. It only adds a refusal: a body keyword
in a sentence with `not`, `never`, `no`, `neither`, `nor`, `nothing`,
`without`, `cannot` or `n't` is refused, because its author probably did
not mean the close. Code spans and fences in a body are skipped for
this.

**With `--pr N`** it reads the pull request's commits, title, body,
commit count and `closingIssuesReferences` from GitHub, and refuses:

* a commit-message keyword closing an issue the pull request doesn't
  declare (commit bodies; subjects too with `--include-subject`). At one
  commit, the squash message is that commit's message; at two or more,
  the bodies are joined beneath the title, so the count is no defence;
* a title keyword, at two or more commits (when the title is the squash
  subject), that the body does not declare;
* a body keyword in a sentence that denies it.

It prints what it read and the whole set the merge will close:

      3 commit(s) examined, from pull request 34's commits
      commit messages would close: #12
      the pull request declares:   #12
      will close on merge:         #12
        from the body: #12
        from the commit messages: #12
    forge-closing-keywords: 1 issue(s) closed by a commit message, subject included, 0 not declared, 0 closed from a denial

Each finding goes to stderr with its remedy: declare the issue in the
body, reword a subject on a new branch, retitle, or reword a denial.
For a denial it says not to declare the issue, which would pass the
check and still close an issue its author said to leave open.

**Exit 3, GitHub lagging.** When every issue refused is declared by a
keyword in the body but `closingIssuesReferences` doesn't list it yet,
the refusal says to wait and run again. That field can lag a body edit
by minutes. Nothing needs rewording.

**Without `--pr`** it reads commit messages from the range in the
checkout and reports what they close, but compares them with no
declaration and reads no body; the summary says so and suggests
`--pr N`. A subject keyword is reported, not refused, unless
`--include-subject` is given. `--include-subject` without `--pr`, on a
branch that has an open pull request, is exit 2 and names it.

**With `--below M`** each `M` must have merged; their commits are read
from GitHub and dropped from this pull request's, since their keywords
closed their issues when they merged, and their own run of this check
judged them.

**With a range and `--pr`**, the range must hold every one of the pull
request's commits, and none already on its base branch. A range using
`A...B` (three dots) usually fails the second test; use two dots.

**`--check-body FILE`** reads one local file and reports, per keyword,
`FILE:line:column`. On `--surface body`, code spans and fences disarm
a keyword, as GitHub reads a body; a denial in a sentence is refused.
On `--surface commit`, code is not known to disarm one in a commit
message, so a keyword in code still counts, and the report says so. A
keyword on line 1 of a commit message (the subject) is reported, not
refused. A declared issue no keyword closes is noted; it may be closed
from a commit message, which this mode can't see.

GitHub's synthetic `Merge <sha> into <sha>` commit is ignored
throughout. A range with no commits is exit 2, since it would read
exactly like a clean branch.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Nothing closes that isn't declared, and nothing closes from a denial. |
| 1 | Something does. |
| 2 | The question could not be asked: no configuration, an unreadable range or file, a pull request whose commits, declaration, title or body could not be fetched, a number that could not be looked up, a range that isn't that pull request's branch, an empty range, a `--below` pull request that hasn't merged, `--include-subject` on a branch whose open pull request wasn't named, or a usage error. |
| 3 | Every refused issue is declared in the body, and only `closingIssuesReferences` has yet to list it. A refusal for now: run it again in a few minutes. |

## See also

* [Configuration keys](../config.md#keys): `merge.gate` and
  `closing_keywords`.
* [forge-commit-identity](forge-commit-identity.md): the Merger's
  default gate.
* [forge-new-body](forge-new-body.md): writes a signed body to check.
