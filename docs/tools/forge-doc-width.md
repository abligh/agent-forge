# forge-doc-width

`forge-doc-width` checks that Markdown prose is wrapped within the
project's width, 80 columns by default. A document is reviewed as a
diff, and a paragraph written as one long line turns a one-word change
into a whole-paragraph change nobody can review. A limit the tools
enforce settles the question once, instead of in every review.

## Usage

    forge-doc-width [PATH...]

A project runs it with [forge-doc-format](forge-doc-format.md) and
[forge-doc-links](forge-doc-links.md), before pushing and again in CI or
a check step. The [forge-doc-format](forge-doc-format.md#usage) page
shows a Makefile target and `checks.steps` entry that run all three.

```sh
forge-doc-width                  # the files docs.paths names
forge-doc-width README.md docs   # these paths only
```

Wrap prose a little short of the width; the tool fails only past it.

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `PATH...` | the files `docs.paths` matches | Markdown files, or directories searched recursively for `*.md`. A path that does not exist is an error. |

There are no options. Every argument is taken as a path, so `--help` is
not understood: it is reported as a missing file (exit 2). The width
comes only from the configuration.

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The configuration file itself. If it is set and the file is missing, that is an error. |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when `FORGE_CONFIG` is not set. |

With neither set, the configuration is found from the git checkout
around the working directory. With no configuration file, the defaults
are used; a file that exists but is invalid is an error.

## Configuration

It reads [`docs.width`](../config.md#keys), the widest a prose line may
be (default 80, at least 1), and [`docs.paths`](../config.md#keys), the
files to check with no `PATH`. How `docs.paths` is read, and what is
left out, is the same for all three checks; see
[forge-doc-format](forge-doc-format.md#configuration).

## What it does

It reports every line longer than the width, in characters, unless it
is exempt. Exempt, because their line breaks carry meaning or can't be
moved:

* lines in fenced code blocks, opened by ```` ``` ```` or `~~~` and
  closed by the same marker;
* indented code blocks: lines indented four spaces or a tab, starting
  after a blank line. An indented line that continues a list item, with
  no blank line before it, is still prose;
* table rows: lines starting with `|`;
* lines carrying a link (`](`), a link definition (`[name]: target`),
  or a bare `http://` or `https://` URL;
* a line that is a single word, since no wrap could shorten it.

Each long line is printed on stdout, then a summary:

    docs/guide.md:41: longer than 80 columns
    forge-doc-width: 1 line(s) too long (12 files)

A clean run prints `forge-doc-width: clean (N files)`, so a pass can be
told from a check that matched nothing.

It can't see a reflow that lost a word. After an edit meant only to
re-wrap, this prints nothing for a pure reflow, and the lost word for
one that dropped it:

```sh
git diff --word-diff=porcelain -- FILE | grep '^-[^-]'
```

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | No line is too long. |
| 1 | At least one line is too long; each is listed as `path:line`. |
| 2 | The configuration is invalid, a named path does not exist, a file could not be read (or is not UTF-8), or nothing matched a Markdown file. |

## See also

* [forge-doc-format](forge-doc-format.md): whitespace and headings.
* [forge-doc-links](forge-doc-links.md): links and anchors.
* [Configuration: `docs`](../config.md#keys).
