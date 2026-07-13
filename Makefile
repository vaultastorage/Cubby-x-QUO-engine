.PHONY: setup check baseline-dry baseline run-dry run clean
PY := python

setup:
	$(PY) -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

check:
	$(PY) cubby_quo_sync.py check

baseline-dry:
	$(PY) cubby_quo_sync.py baseline

baseline:
	$(PY) cubby_quo_sync.py baseline --commit

run-dry:
	$(PY) cubby_quo_sync.py run

run:
	$(PY) cubby_quo_sync.py run --commit

clean:
	rm -rf .venv __pycache__ baseline_preview.csv
	@echo "Kept .env and state.json on purpose."
