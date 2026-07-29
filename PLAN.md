# PLAN.md — liveflights: Real-Time Flight Intelligence Platform

## What this is

A local, fully-demoable streaming lakehouse for live aircraft data:

```
Producer (simulator | OpenSky) -> Redpanda(flights.raw)
  -> Spark Structured Streaming -> Bronze/Silver/Gold (Delta on MinIO)
  -> Postgres (gold mirror) -> dbt marts
  -> FastAPI (REST + WebSocket) -> Next.js dashboard
Airflow orchestrates batch jobs (compaction, dbt, ML retrain, quality/drift).
MLflow tracks + registers models; FastAPI loads from registry at startup.
```

Two full days of build time. Optimize for "actually runs end to end" over
completeness of any single layer. Everything must come up with `make up`.
Everything must be demoable with zero external API credentials via
`--mode simulate`.

## Tech stack (fixed, no substitutions)

Ingestion: Python + confluent-kafka -> Redpanda
Processing: PySpark Structured Streaming + Delta Lake
Object store: MinIO (S3-compatible), flag to point at real AWS S3
Warehouse: PostgreSQL 16
Transform: dbt-core + dbt-postgres
Orchestration: Airflow (LocalExecutor)
ML: scikit-learn + MLflow
Monitoring: Evidently + Grafana + Prometheus
API: FastAPI + Pydantic v2
Frontend: Next.js 14 App Router (client components) + Tailwind + Leaflet + Recharts
IaC: Terraform (S3 bucket, IAM, Lambda archive job)
CI: GitHub Actions (ruff, pytest, docker build)
Package mgmt: uv (Python), pnpm (frontend)

## Repo layout

```
./
├── CLAUDE.md, PLAN.md, PROGRESS.md, README.md, Makefile, .env.example
├── docker-compose.yml
├── ingestion/        producer, simulator, schemas, tests
├── streaming/        spark bronze/silver/gold jobs, delta utils
├── transform/        dbt project
├── orchestration/    airflow dags + plugins
├── ml/               features, train_anomaly.py, train_forecast.py, evaluate.py
├── api/              FastAPI app (routers, services, models, deps)
├── web/              Next.js app
├── infra/            terraform/ + grafana/ + prometheus/
├── tests/            pytest — unit + integration
├── docs/             architecture.md, decisions.md, screenshots/
└── .github/workflows/ci.yml
```

## Data contract (flights.raw / all layers)

`icao24, callsign, origin_country, time_position, last_contact, longitude,
latitude, baro_altitude, on_ground, velocity, true_track, vertical_rate,
geo_altitude, squawk, spi, position_source` — plus ingest metadata
(`ingest_ts`, `source` = simulate|opensky, `ingest_date`, `ingest_hour`).

## Medallion design

- **Bronze**: raw payload as-is + ingest metadata. Parquet, partitioned by
  `ingest_date/ingest_hour`. Append-only, no dedup, no typing beyond JSON parse.
- **Silver**: Delta table. Deduped on `(icao24, time_position)`. Typed columns.
  Enriched: `speed_kmh`, `altitude_ft`, `flight_phase`
  (ground/climb/cruise/descent from vertical_rate + on_ground),
  `geohash5`, `continent`/`region` bucket (from lat/lon), `data_quality_flags`
  (array: missing_position, stale_contact, implausible_speed, etc).
- **Gold**: Delta + mirrored to Postgres.
  - `traffic_by_hour` (flight count, avg altitude, avg speed, bucketed by hour)
  - `traffic_by_country` (origin_country rollups)
  - `airline_activity` (callsign prefix -> airline via static lookup table)
  - `altitude_band_distribution` (banded histogram)
  - `anomaly_events` (scored flights from the anomaly model)

## ML design

1. **Anomaly detection** — `IsolationForest` on
   `[velocity, baro_altitude, vertical_rate, track_delta, altitude_delta]`.
   Output `anomaly_score` (continuous) + `is_anomaly` (bool) into gold.
   Metrics logged: contamination rate, silhouette score on a held-out sample.
2. **Traffic forecast** — `GradientBoostingRegressor` on lag features
   (t-1, t-2, t-3, t-24) + hour-of-day + day-of-week, predicting flight count
   for the next 6 hourly buckets. Metrics: MAE, RMSE, MAPE.

Both log params/metrics/artifacts to MLflow tracking, register to the Model
Registry, and the best run per model is promoted to stage `Production`.
FastAPI loads from registry at startup; falls back to a local pickle if the
registry/model is unavailable (keeps the API demoable even if MLflow is down).

## Airflow DAGs

1. `hourly_compaction` — Delta OPTIMIZE/VACUUM on silver+gold, refresh gold
   aggregates, upsert into Postgres.
2. `daily_dbt` — `dbt run` + `dbt test` + `dbt docs generate`.
3. `daily_ml_retrain` — retrain both models, log to MLflow, conditionally
   promote to Production if metrics improve over current Production model.
4. `daily_quality_drift` — data quality checks (row counts, null rates,
   schema drift) + Evidently HTML drift report + sync gold snapshot to S3
   (real AWS, via the S3/MinIO abstraction flag).

## API surface

`GET /health`, `GET /api/flights/live` (bbox + limit), `GET
/api/stats/overview`, `GET /api/stats/traffic-by-hour`, `GET
/api/stats/by-country`, `GET /api/anomalies` (paginated), `POST
/api/predict/anomaly`, `GET /api/forecast/traffic`, `WS /ws/flights` (push
every 3s). Cross-cutting: CORS, request-ID middleware, structured JSON
logging, Prometheus `/metrics`, Redis caching (60s TTL) on stats endpoints,
full OpenAPI descriptions.

## Frontend

Single-page dark dashboard: full-width Leaflet map with rotated plane icons
colored by altitude band, live over WebSocket, click-to-detail popup; 4 KPI
cards; traffic-by-hour + forecast overlay chart, top-countries bar, altitude
histogram; anomaly feed panel (click focuses the plane on the map); pipeline
health strip (last DAG run, freshness, records processed). Loading/error/empty
states everywhere. Must look portfolio-ready on first screenshot.

## Phases (execute in order; update PROGRESS.md after each)

- **P1** Scaffold: repo structure, docker-compose (redpanda, minio, postgres,
  redis, mlflow, grafana, prometheus), Makefile (up/down/logs/seed/test),
  .env.example, uv project, CLAUDE.md, PLAN.md. Verify all containers healthy.
- **P2** Ingestion: simulator + OpenSky client + Kafka producer + schema
  validation + DLQ + unit tests. Verify messages landing in `flights.raw`.
- **P3** Streaming: Spark bronze -> silver -> gold, Delta on MinIO,
  checkpointing, Postgres sink. Verify data in all three layers.
- **P4** dbt: staging/intermediate/marts/tests/docs. Verify `dbt test` passes.
- **P5** ML: feature engineering, both models, MLflow logging + registry
  promotion.
- **P6** API: all endpoints + WebSocket + caching + metrics + pytest.
- **P7** Frontend: full dashboard wired to the API.
- **P8** Airflow: 4 DAGs, verify each runs green.
- **P9** Ops: Terraform, GitHub Actions, Grafana dashboards, Evidently report.
- **P10** Docs: README (mermaid architecture diagram, setup, screenshots
  placeholders, metrics table, tech stack list).

## Environment notes (discovered during setup)

- Docker Desktop and `uv` were **not** pre-installed on this machine despite
  the brief assuming they were; both were installed via Homebrew during P1
  setup (with the user's help for the sudo-gated symlink steps). Documented
  here so future sessions don't re-assume a from-scratch environment.
- Docker Desktop app must be running (`open -a Docker`) before `make up`.

## Rules carried through every phase

- Type hints + docstrings everywhere, ruff-clean.
- All config via `pydantic-settings` from `.env`. No hardcoded secrets/paths.
- Every service starts from a single `make up`.
- Prefer small working code over clever code.
- Run and show real verification output after each phase — never claim
  something works without executing it.
- On a blocker: implement the simplest working fallback, note it in
  PROGRESS.md, keep moving.
- **No git commands, ever.** No `.gitignore` created directly — recommended
  contents go in `docs/gitignore-recommended.txt`. Print a "READY TO COMMIT"
  block (files changed + suggested conventional-commit message) at the end of
  each phase.
