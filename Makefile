# Everything a change must pass. Standard library Python only.
PYTHON ?= python3

.PHONY: all test check-neutral replay-compare
all: test check-neutral

test:
	$(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -t . -q

check-neutral:
	tools/check-neutral

# The library's days replayed at two versions, as a Markdown table
# (docs/merger-sim.md). DAYS: library kinds or trace files.
BEFORE ?= origin/main
AFTER ?= .
DAYS ?= ordinary heavy bad
replay-compare:
	$(PYTHON) -m tests.merger_compare --before $(BEFORE) --after $(AFTER) \
		$(foreach d,$(DAYS),--day $(d))
