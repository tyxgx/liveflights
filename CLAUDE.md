# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repo.

## What this repo is

See `PLAN.md` for the full architecture and phase plan, and `PROGRESS.md` for
what's actually been built so far. This is a local-first streaming lakehouse
demo (OpenSky flight data -> Redpanda -> Spark/Delta -> Postgres/dbt -> ML ->
FastAPI -> Next.js), fully runnable via `make up` with zero cloud credentials
required (simulator mode).

## Ground rules


- **Never create `.gitignore` directly.** Recommended contents live in
  `docs/gitignore-recommended.txt`.
- Config comes from `.env` via `pydantic-settings` — no hardcoded secrets or
  absolute paths. `.env.example` must stay in sync with every config field
  any service reads.
- Type hints + docstrings on all Python. Code must be ruff-clean
  (`ruff check .`).
- `uv` for Python dependency management (`uv sync`, `uv run`), `pnpm` for the
  frontend. Don't introduce pip/poetry/npm/yarn.
- Every service must come up from a single `make up`. If something doesn't,
  fix the compose file / entrypoint rather than documenting a manual step.
- After implementing a phase, actually run it and show real output
  (container health, test results, sample records) — don't claim success
  without executing it.
- On a blocker (missing credential, flaky dependency, arm64 image gap):
  implement the simplest working fallback, note it in `PROGRESS.md`, and keep
  moving rather than stopping to ask.
- Prefer small, boring, working code over abstraction. This is a 2-day build;
  don't add speculative generality.

## Environment specifics (this machine)

- macOS (Apple Silicon / arm64), fish shell, Python 3.12, Docker Desktop.
- All container images must have arm64 support.
- Docker Desktop and `uv` had to be installed via Homebrew at project start —
  don't assume a from-scratch dev machine has them; `make up` should still be
  the only required step once they exist.
- AWS credentials live in `~/.aws/credentials`, region `us-east-1`. Used only
  when `STORAGE_BACKEND=s3` (default is `minio` for local dev) and for the
  Terraform/Lambda archive job and the drift DAG's S3 sync.

## Commands

```
make up        # docker compose up -d, wait for health checks
make down       # docker compose down
make logs       # tail all service logs
make seed       # run the simulator producer to seed flights.raw
make test       # ruff + pytest (unit + integration where containers are up)
```

Python: `uv run <script>` inside the relevant subproject (`ingestion/`,
`streaming/`, `ml/`, `api/`, each has its own `pyproject.toml` if isolation is
needed, otherwise a root `pyproject.toml` covers shared deps).

Frontend: `cd web && pnpm install && pnpm dev`.

## Directory map

- `ingestion/` — producer, `--mode simulate|opensky`, schemas, DLQ, tests.
- `streaming/` — Spark Structured Streaming jobs, bronze/silver/gold, Delta
  utils, checkpoint config.
- `transform/` — dbt project (staging -> intermediate -> marts).
- `orchestration/` — Airflow DAGs + plugins (4 DAGs, see PLAN.md).
- `ml/` — feature engineering + training scripts for both models, MLflow.
- `api/` — FastAPI app: `routers/`, `services/`, `models/` (Pydantic), `deps/`.
- `web/` — Next.js 14 App Router dashboard, client components only.
- `infra/` — `terraform/`, `grafana/` dashboards, `prometheus/` config.
- `tests/` — cross-cutting pytest (unit + integration).
- `docs/` — architecture doc, decisions log, screenshots, gitignore recommendation.

## Data contract

Canonical flight state fields (used from ingestion through gold):
`icao24, callsign, origin_country, time_position, last_contact, longitude,
latitude, baro_altitude, on_ground, velocity, true_track, vertical_rate,
geo_altitude, squawk, spi, position_source`, plus ingest metadata (`ingest_ts`,
`source`, `ingest_date`, `ingest_hour`). Don't rename these fields casually —
they're referenced across ingestion, Spark schemas, dbt sources, and the API's
Pydantic models.
