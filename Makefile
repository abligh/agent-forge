# Everything a change must pass. Standard library Python only.
PYTHON ?= python3

.PHONY: all test check-neutral
all: test check-neutral

test:
	$(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -t . -q

check-neutral:
	tools/check-neutral
