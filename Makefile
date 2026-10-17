PYTHON ?= python

.PHONY: up down migrate demo test lint fmt

## Start the local services (Postgres 16, MinIO) and wait until they are healthy.
up:
	docker compose up -d --wait

## Stop the local services. Data lives in named volumes and is kept.
down:
	docker compose down

## Apply db/migrations in order. Re-running applies nothing.
migrate:
	$(PYTHON) -m db.migrate

## Run ProcessLens locally for a person: migrations, API, and a supervisor that starts workers.
## Forward flags with ARGS, e.g. `make demo ARGS=--extension-id <id>`.
demo:
	$(PYTHON) scripts/demo.py $(ARGS)

## Run the test suite.
test:
	$(PYTHON) -m pytest

## Check the Python code for lint errors.
lint:
	$(PYTHON) -m ruff check .

## Format the Python code.
fmt:
	$(PYTHON) -m ruff format .
