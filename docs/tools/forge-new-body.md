# forge-new-body

`forge-new-body` writes a body file for a GitHub issue, pull request
or comment from standard input, with the agent's sign-off appended as
its last line. Agent sessions may share one `gh` login, so on GitHub the
sign-off, `— <Agent>`, can be the only thing that says which agent
wrote a body. The tool makes it hard to post one unsigned, signed
twice, or signed as someone else, and it refuses an `@`-mention, which
would notify a stranger who happens to hold that GitHub login.

## Usage

    forge-new-body NAME < body.txt

An agent runs it in its session, then posts the file with
[forge-gh-signed](forge-gh-signed.md):

```sh
export FORGE_AGENT=LeadDev
path=$(forge-new-body fix-12-comment <<'EOF'
The parser now rejects an empty header. Tested with `make test`.
EOF
)
forge-gh-signed issue comment 12 --body-file "$path"
```

`FORGE_AGENT` is normally set once, in the `env` of the agent's Claude
Code settings, rather than on each command.

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | required | The draft's name. The file written is `NAME.md`; a `NAME` that already ends in `.md` is not doubled. It must not be empty or contain `/`. |

There are no other options. The body is read from standard input.

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_AGENT` | Required. The agent signing: a bare name of letters, digits, `_` or `-`. Never guessed. |
| `FORGE_BODY_DIR` | The directory to write into. Default `${TMPDIR:-/tmp}/agent-forge-bodies/<agent>`. |
| `TMPDIR` | Used for the default directory when `FORGE_BODY_DIR` is not set; without it, the system's temporary directory (usually `/tmp`). |

## Configuration

None. It does not read `.agent-forge/config.json`.

## What it does

1. It reads the whole of standard input.
2. It strips any trailing blank lines and any trailing sign-off lines
   (`— <Agent>`, however many), then appends a blank line and exactly
   one sign-off. A caller who signs too still gets one, and a sign-off
   quoted earlier in the body can't stand in for the real one. The dash
   is U+2014, the em dash.
3. It refuses the body (exit 4) if it holds an `@`-mention outside code.
   Fenced blocks and inline code spans are not searched, unless the
   fences don't pair, in which case the whole body is. A mention is `@`
   then a letter, at a word boundary, so `pkg@1.0` and
   `example/widget@abc123` are not mentions. Each one found is listed on
   stderr with its line number, counted over the text outside code.
4. It checks that the result ends with exactly one sign-off (exit 3 if
   not; this is a safety check that should not fail).
5. It writes the file and prints its path on stdout.

**Where.** Outside any repository, so `git add -A` can't stage a draft,
and in a directory per agent, so two agents writing a draft of the same
name don't post each other's text. Drafts persist, so they can be found
again. An existing file of the same name is overwritten.

**A refused body is never written**, and an older file of the same
name is removed. So a `forge-gh-signed ... --body-file` that follows a
refusal fails, instead of posting the refused text or an older draft.

**A backticked handle**, such as `` `@someone` ``, is allowed but
warned about on stderr. It is inert in a comment, but not in a commit
message, which GitHub doesn't render as Markdown: a single-commit pull
request's squash message carries the handle live.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Written; the path is on stdout. |
| 1 | The file or its directory could not be written. |
| 2 | Usage (no `NAME`, more than one argument, or a `/` in it), or `FORGE_AGENT` unset or not a bare name. |
| 3 | The sign-off check failed. Nothing written. |
| 4 | The body holds an `@`-mention outside code. Nothing written. |

## See also

* [forge-gh-signed](forge-gh-signed.md): posts the file as the agent's
  App, checking the sign-off again.
