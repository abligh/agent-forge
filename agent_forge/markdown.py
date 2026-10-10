"""The Markdown files the documentation checks read, and their settings.

forge-doc-format, forge-doc-width and forge-doc-links read the same
files, chosen the same way, so they share it here:

* paths named on the command line, each a Markdown file or a directory
  searched for `*.md` recursively; a path that doesn't exist is an
  error, so a typo is reported rather than silently checking less;
* else the configuration's `docs.paths`, globs relative to the
  project's checkout. A glob that matches nothing is skipped, so a
  project can list a directory it doesn't have yet;
* and either way, matching no Markdown file at all is an error: a check
  that read nothing must not pass as one that found nothing wrong.

The configuration is optional for these tools. They are useful in any
checkout, so where there is no configuration file they use the
defaults; a configuration that exists but is wrong is still an error.
"""
import glob
import os
import re

from agent_forge import config, proc

FENCE_RE = re.compile(r"^\s*(```|~~~)")
DEFAULT_PATHS = ["*.md", "docs"]
DEFAULT_WIDTH = 80


class Settings:
    """The `docs` section, or the defaults with no configuration, and
    the directory relative paths in it are read from."""

    def __init__(self, section=None, root=None):
        self.section = section or {}
        self.root = root or os.getcwd()

    @property
    def paths(self):
        return self.section.get("paths", DEFAULT_PATHS)

    @property
    def width(self):
        return self.section.get("width", DEFAULT_WIDTH)


def settings(environ=None, cwd=None, run=proc.sh):
    """The Settings for the checkout around `cwd`. ConfigError if a
    configuration exists but isn't valid, or $FORGE_CONFIG names a file
    that doesn't exist."""
    environ = os.environ if environ is None else environ
    try:
        path = config.find(environ, cwd, run)
    except config.ConfigError:
        return Settings()  # not in a checkout: nothing to configure
    if not environ.get("FORGE_CONFIG") and not os.path.exists(path):
        # No configuration: the defaults, read from the checkout's top.
        return Settings(root=path[:-len(os.sep + config.CONFIG_NAME)])
    project = config.load(path)
    return Settings(project.section("docs"), project.root)


def markdown_files(paths):
    """The Markdown files named by `paths`, directories expanded.

    Raises FileNotFoundError for a path that does not exist, so a typo
    is reported rather than silently checking less.
    """
    found = []
    for path in paths:
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                found += [os.path.join(dirpath, name)
                          for name in sorted(filenames)
                          if name.endswith(".md")]
        elif os.path.exists(path):
            found.append(path)
        else:
            raise FileNotFoundError(f"{path}: no such file or directory")
    return found


def expand(patterns, root):
    """The paths `patterns` (globs, `**` allowed) match under `root`,
    relative to the working directory where that is shorter to read.
    A pattern that matches nothing is skipped."""
    found = []
    for pattern in patterns:
        for match in sorted(glob.glob(pattern, root_dir=root,
                                      recursive=True)):
            path = os.path.relpath(os.path.join(root, match))
            if path not in found:
                found.append(path)
    return found


def files(argv_paths, docs):
    """The Markdown files to check: `argv_paths` if any were given,
    else the configured globs. FileNotFoundError for a named path that
    doesn't exist."""
    if argv_paths:
        return markdown_files(argv_paths)
    return markdown_files(expand(docs.paths, docs.root))


def choose(prog, argv_paths, err, environ=None, run=proc.sh):
    """(files, Settings) for a check called `prog`, or the exit status
    2 after saying why on `err`: an invalid configuration, a missing
    path, or no Markdown file at all."""
    try:
        docs = settings(environ, run=run)
    except config.ConfigError as exc:
        print(f"{prog}: {exc}", file=err)
        return 2
    try:
        found = files(argv_paths, docs)
    except FileNotFoundError as exc:
        print(f"{prog}: {exc}", file=err)
        return 2
    if not found:
        where = " ".join(argv_paths) if argv_paths else \
            "docs.paths (%s)" % " ".join(docs.paths)
        print(f"{prog}: no Markdown files under {where}; nothing was "
              "checked", file=err)
        return 2
    return found, docs

