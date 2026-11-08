# forge-doc-links

`forge-doc-links` checks that every relative link in a set of Markdown
files resolves, and that every `#anchor` matches a heading. A broken
link is invisible until a reader clicks it, and a moved file or a
renamed heading breaks every link to it without touching the files
that hold them. Only a check over the whole tree sees that.

## Usage

    forge-doc-links [PATH...]

A project runs it with [forge-doc-format](forge-doc-format.md) and
[forge-doc-width](forge-doc-width.md), before pushing and again in CI or
a check step. The [forge-doc-format](forge-doc-format.md#usage) page
shows a Makefile target and `checks.steps` entry that run all three.

```sh
forge-doc-links                  # the files docs.paths names
forge-doc-links docs/guide.md    # this file's links only
```

Links are followed into any file, so naming one file still checks that
the files it links to exist and hold the anchors it names.

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
around the working directory. With no configuration file, the defaults
are used; a file that exists but is invalid is an error.

## Configuration

It reads [`docs.paths`](../config.md#keys), the files to check with no
`PATH`. How it is read, and what is left out, is the same for all three
checks; see [forge-doc-format](forge-doc-format.md#configuration). The
top of the project's checkout, where `/`-rooted links resolve, is the
directory holding `.agent-forge/`, or the top of the git checkout when
there is no configuration file.

## What it does

It reads each file's prose: lines in fenced code blocks are skipped,
and inline code spans are removed, so an example of a link is not a
link. It finds:

* inline links `[text](target)` and images `![alt](target)`, with an
  optional title;
* link definitions, `[name]: target`.

For each target:

* a target with a scheme (`http:`, `https:`, `mailto:` or any other)
  is not checked, since that needs the network and a flaky check gets
  ignored;
* the path, percent-decoded, must exist. It resolves against the
  linking file's directory, or against the top of the checkout when it
  starts with `/`, as GitHub resolves it. An empty path (`#section`)
  means the linking file itself;
* an anchor into a Markdown file must match one of its headings, by
  GitHub's slug rules, or an explicit `<a id="...">` or
  `<a name="...">`. Case does not matter. Anchors into files that are
  not Markdown, or into a directory, are not checked.

GitHub's slug rules, as applied: a link in the heading counts as its
text; HTML tags are dropped; the text is lower-cased; every character
that is not a letter, digit, `_`, `-` or space is dropped; each space
becomes `-`. A repeated heading gets `-1`, `-2` and so on. So
`## What it does` is `#what-it-does`. Headings inside fenced code are
not anchors.

One difference from GitHub: inline code is removed before a heading is
read, so a heading that is all code, such as ``### `docs` ``, has an
empty anchor here, though GitHub gives it `#docs`. Link to a nearby
heading instead.

Each broken link is printed on stdout, then a summary:

    docs/guide.md:12: links to setup.md, which does not exist
    docs/guide.md:20: links to config.md#keyz, which matches no heading
    forge-doc-links: 2 broken link(s) (57 links, 12 files)

A clean run prints `forge-doc-links: clean (N links, M files)`.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Every link resolves. |
| 1 | At least one does not; each is listed as `path:line: message`. |
| 2 | The configuration is invalid, a named path does not exist, a file could not be read (or is not UTF-8), or nothing matched a Markdown file. |

## See also

* [forge-doc-format](forge-doc-format.md): whitespace and headings.
* [forge-doc-width](forge-doc-width.md): line width.
* [Configuration: `docs`](../config.md#keys).
