# forge-doc-format

`forge-doc-format` checks Markdown files for whitespace and heading
mistakes: trailing whitespace, a missing or doubled final newline, and
headings out of order. Trailing whitespace is invisible in review and
shows up as noise in the next diff; two trailing spaces are also a
hidden Markdown line break. A skipped heading level breaks the outline
GitHub renders and the anchors other documents link to. The tool checks
only; it never rewrites a file.

## Usage

    forge-doc-format [PATH...]

A project runs it with [forge-doc-width](forge-doc-width.md) and
[forge-doc-links](forge-doc-links.md), before pushing and again in CI or
a check step, so a pull request can't merge because someone forgot. For
example, as a Makefile target that a `checks.steps` entry runs:

```make
doc-check:
	forge-doc-format
	forge-doc-width
	forge-doc-links
```

```json
{"name": "doc-check", "run": ["make", "doc-check"]}
```

Run it from inside the project's checkout. With no `PATH`, it checks the
files the configuration's `docs.paths` names.

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `PATH...` | the files `docs.paths` matches | Markdown files, or directories searched recursively for `*.md`. A path that does not exist is an error. |

There are no options. Every argument is taken as a path, so `--help` is
not understood: it is reported as a missing file (exit 2).

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The configuration file itself. If it is set and the file is missing, that is an error. |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when `FORGE_CONFIG` is not set. |

With neither set, the configuration is found from the git checkout
around the working directory. Unlike most tools, these checks work
without a configuration: with no file, they use the defaults. A file
that exists but is invalid is still an error.

## Configuration

It reads [`docs.paths`](../config.md#keys): globs relative to the top
of the checkout (`**` crosses directories), each a Markdown file or a
directory. The default is `["*.md", "docs"]`. With no configuration,
the globs are read from the top of the git checkout, or the working
directory outside one.

When the files come from `docs.paths`, anything git ignores is left out,
such as a dependency's Markdown under `node_modules`. A glob that
matches nothing is skipped. A path named on the command line is always
read.

## What it does

For each file, it reports:

* **trailing whitespace** on any line, code blocks included (a tab or a
  carriage return counts);
* **no newline at end of file**, or **blank line(s) at end of file**;
* **file is empty**, for an empty file;
* **the first heading is not level 1** (a single `#`);
* **a second level-1 heading**: a document has one `#` title;
* **a heading that jumps a level**, such as `##` then `####`; the
  message names the level to use. Going back up any number of levels is
  fine.

A heading is one to six `#` then a space or the end of the line. Lines
inside fenced code blocks (opened by ```` ``` ```` or `~~~` and closed
by the same marker) are not headings.

Each finding is printed on stdout as `path:line: message`, then a
summary:

    docs/guide.md:14: trailing whitespace
    docs/guide.md:30: heading jumps from level 2 to 4; use ###
    forge-doc-format: 2 problem(s) (12 files)

A clean run prints `forge-doc-format: clean (N files)`, so a pass can
be told from a check that read nothing.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Every file is clean. |
| 1 | At least one finding; each is listed as `path:line: message`. |
| 2 | The configuration is invalid, a named path does not exist, a file could not be read (or is not UTF-8), or nothing matched a Markdown file. |

## See also

* [forge-doc-width](forge-doc-width.md): line width.
* [forge-doc-links](forge-doc-links.md): links and anchors.
* [Configuration: `docs`](../config.md#keys).
