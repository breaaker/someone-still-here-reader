.PHONY: status build validate test check

status:
	python3 scripts/reader_build.py status

build:
	python3 scripts/reader_build.py build

validate:
	python3 scripts/reader_build.py validate

test:
	python3 -m unittest discover -s tests -v

check: validate test
