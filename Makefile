.PHONY: up down logs ps seed test lint fmt clean

COMPOSE = docker compose

up:
	$(COMPOSE) up -d
	@echo "Waiting for core services to report healthy..."
	@for i in $$(seq 1 60); do \
		unhealthy=$$($(COMPOSE) ps --format '{{.Name}} {{.Health}}' | grep -v healthy | grep -v 'N/A$$' || true); \
		if [ -z "$$unhealthy" ]; then echo "All services healthy."; exit 0; fi; \
		sleep 2; \
	done; \
	echo "Timed out waiting for services:"; $(COMPOSE) ps

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

seed:
	uv run python -m ingestion.producer --mode simulate --once

test:
	uv run ruff check .
	uv run --group streaming --group dbt pytest -q

dbt-debug:
	set -a && . ./.env && set +a && uv run --group dbt dbt debug --project-dir transform --profiles-dir transform

dbt-run:
	set -a && . ./.env && set +a && uv run --group dbt dbt run --project-dir transform --profiles-dir transform

dbt-test:
	set -a && . ./.env && set +a && uv run --group dbt dbt test --project-dir transform --profiles-dir transform

dbt-snapshot:
	set -a && . ./.env && set +a && uv run --group dbt dbt snapshot --project-dir transform --profiles-dir transform

dbt-docs:
	set -a && . ./.env && set +a && uv run --group dbt dbt docs generate --project-dir transform --profiles-dir transform

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

clean:
	$(COMPOSE) down -v
