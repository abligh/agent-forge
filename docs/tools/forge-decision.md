# forge-decision

`forge-decision` says whether the repository's owner decided something,
on one issue or pull request, after it was asked. A comment by the
owner there is a **decision of record**: an instruction written once,
where every agent can read it, rather than repeated in each session.
The tool answers only whether such a comment exists, and prints it.
Reading the words is the agent's job. A project wants it so that no
agent acts on a decision the owner never made, or one made before the
question was put.

## Usage

    forge-decision --issue N --answering COMMENT_ID
    forge-decision --issue N --answering body
    forge-decision --issue N --answering ID|body --repo OWNER/NAME

An agent runs it in its session before acting on something that needs
the owner's say, such as an approval request:

```sh
forge-decision --issue 71 --answering 5696737727
forge-decision --issue 12 --answering body
```

`--answering body` names the item's own body as the question, as for an
approval request whose body is the question. Then the ask's time is the
issue's `created_at`.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--issue N` | required | The issue or pull request number. |
| `--answering ID\|body` | required | The ask the decision must answer: a comment id on that same item, or `body` for the item's own body. Anything else is refused. |
| `--repo OWNER/NAME` | the configuration's `repo` | The repository to read. Needed when there is no configuration. |
| `-h`, `--help` | | Print the usage and exit. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_AGENT` | If set, and its App's token mints, GitHub is read as that App. |
| `GH_TOKEN` | Else this token is used for reading. Else it reads anonymously, which public repositories allow at a low rate. |
| `FORGE_CONFIG` | The configuration file itself. |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when `FORGE_CONFIG` is not set. |
| `FORGE_GITHUB_API` | The GitHub API base URL. Default `https://api.github.com`. |

With no configuration found, `--repo` is required and only `GH_TOKEN`
(or nothing) is used to read.

## Configuration

It reads [`repo`](../config.md#keys) for the default `--repo`, and the
App fields of `FORGE_AGENT` (see
[forge-app-token](forge-app-token.md#configuration)) to read as that
App. It does not use the configuration's `owner`: the owner is the
repository's own `owner.login`, read from GitHub.

## What it does

It reads the repository (`GET /repos/<repo>`) for its owner's login,
every comment on the item (all pages), and with `--answering body` the
item itself. A comment is a decision of record when all of these hold:

1. `user.type` is `User`, not `Bot`, so no agent can write one, since
   agents post as their Apps' bots;
2. `author_association` is `OWNER`, which GitHub computes from the
   repository's permissions;
3. `user.login` is the repository's `owner.login`, so a second account
   with owner permissions doesn't count, and a transfer breaks the check
   visibly rather than widening it;
4. it was created after the ask. A decision can't come before its
   question.

This holds only because agents write through their own Apps. An agent
writing with a person's `gh` login would post as that person, and this
could not tell them apart.

**An edited candidate fails the whole answer** with exit 2, not just
itself: a comment whose `updated_at` differs from its `created_at` no
longer shows what was written. The remedy is to ask the owner for a
fresh comment.

For each decision found, it prints:

    decision: comment 5696741234 by example-owner at 2026-10-02T09:15:00Z
      https://github.com/example/widget/issues/71#issuecomment-5696741234
      | Yes, go ahead with the second option.
    forge-decision: 1 decision(s) of record. Quote the comment id in whatever you write; the words are yours to read.

With none, it says so on stderr and exits 1. It only reads; it never
writes to GitHub.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | A decision of record exists; each is printed. |
| 1 | None does. |
| 2 | It could not tell: the configuration was unreadable and there was no `--repo`, the API failed, the named comment is not on that item, a candidate was edited, or a usage error (including an `--answering` that is neither a number nor `body`). A failure never reads as "no". |

## See also

* [forge-app-token](forge-app-token.md): how agents get their own Apps,
  which this relies on.
* [Configuration keys](../config.md#keys): `repo`.
