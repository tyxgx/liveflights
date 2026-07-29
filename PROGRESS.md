# PROGRESS.md

Tracks what's actually been built and verified, phase by phase. See
`PLAN.md` for the full plan and `CLAUDE.md` for conventions.

## P1 — Scaffold ✅ (2026-07-28)

- Full repo structure created per `PLAN.md` layout.
- `docker-compose.yml`: redpanda, redpanda-console, minio (+ minio-init
  bootstrap job), postgres 16, redis, mlflow (backed by postgres + minio
  artifact store), prometheus, grafana. All images verified arm64-compatible
  (Apple Silicon).
- Prometheus config (`infra/prometheus/prometheus.yml`) scrapes itself,
  redpanda, and a placeholder target for the API (added in P6).
- Grafana provisioned with Prometheus + Postgres datasources and a dashboard
  provider pointed at `infra/grafana/dashboards/` (dashboards themselves land
  in P9).
- Postgres bootstrap creates `gold`, `staging`, `marts` schemas.
- `Makefile`: `up` (compose up + poll for healthy), `down`, `logs`, `ps`,
  `seed`, `test`, `lint`, `fmt`, `clean`.
- Root `pyproject.toml` (uv-managed), ruff config, pytest config.
- `.env.example` covers every config value read anywhere in the codebase so
  far; `.env` created locally from it.
- `docs/gitignore-recommended.txt` written (no `.gitignore` created directly,
  per project rules).

**Blocker hit & resolved:** neither Docker Desktop nor `uv` were actually
installed on this machine, despite the brief treating them as pre-existing.
Installed both via Homebrew with the user's help clearing stale root-owned
symlinks from a prior Docker uninstall (`/usr/local/bin/compose-bridge`,
`/usr/local/cli-plugins/docker-compose`) that required interactive sudo.
Documented in `PLAN.md` so future sessions don't assume a bare machine still
needs this.

**Verification performed:**
```
$ docker compose config --quiet && echo "compose file valid"
compose file valid

$ make up
... All services healthy.

$ docker compose ps
liveflights-grafana            Up (health n/a, but curl /api/health -> 200)
liveflights-minio              Up (healthy)
liveflights-mlflow             Up (healthy)
liveflights-postgres           Up (healthy)
liveflights-prometheus         Up (health n/a, but curl /-/healthy -> 200)
liveflights-redis              Up (healthy)
liveflights-redpanda           Up (healthy)
liveflights-redpanda-console   Up
liveflights-minio-init         Exited (0)   # one-shot bucket bootstrap, succeeded

$ curl -s http://localhost:5500/health   -> OK
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:9090/-/healthy -> 200
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:3001/api/health -> 200
$ docker exec liveflights-redpanda rpk topic create flights.raw flights.raw.dlq -p 3 -r 1
flights.raw      OK
flights.raw.dlq  OK
```

## P2 — Ingestion ✅ (2026-07-28)

- `ingestion/config.py` — `pydantic-settings` config, all fields from `.env`.
- `ingestion/schemas/flight_state.py` — canonical `FlightState` Pydantic
  model (OpenSky field layout + ingest metadata: `ingest_ts`, `source`,
  derived `ingest_date`/`ingest_hour`).
- `ingestion/airports.py` — 20 real European airport coordinates for the
  simulator's routes.
- `ingestion/simulator.py` — `FlightSimulator`: great-circle interpolation
  (spherical law-of-cosines slerp) between origin/destination airports,
  climb/cruise/descent altitude profile, realistic velocity/vertical-rate
  noise, ~2% (configurable) injected anomalies (altitude spikes, velocity
  spikes, vertical-rate spikes, missing position, emergency squawks).
  Aircraft respawn with a new route on completion.
- `ingestion/opensky.py` — `OpenSkyClient`: OAuth2 client-credentials token
  fetch with a hard fallback to anonymous polling (longer interval) on
  missing credentials or auth failure — never raises, only logs a warning.
- `ingestion/producer.py` — `FlightProducer`: validates every raw record
  against `FlightState`; valid records go to `flights.raw`, invalid ones go
  to `flights.raw.dlq` with the raw payload + validation error attached.
  CLI: `python -m ingestion.producer --mode simulate|opensky [--once]`.
- Unit tests (`ingestion/tests/`): simulator output shape/schema validity,
  anomaly-rate sanity check, aircraft respawn behavior, producer valid/DLQ
  routing (Kafka client mocked, no real broker needed for unit tests).

**Verification performed:**
```
$ uv sync                     # 24 packages installed, no errors
$ uv run ruff check .          -> All checks passed!
$ uv run pytest -q             -> 6 passed in 1.75s

$ uv run python -m ingestion.producer --mode simulate --once
Starting producer in mode=simulate interval=15s
Published batch: sent=150 dead_lettered=0 total_sent=150 total_dlq=0

$ docker exec liveflights-redpanda rpk topic consume flights.raw -n 1 -o :end --offset -150
{"topic":"flights.raw","key":"a06293","value":"{...icao24, callsign, lat/lon,
altitude, velocity, vertical_rate, squawk, ingest_ts, source:\"simulate\"...}"}

$ docker exec liveflights-redpanda rpk topic describe flights.raw -s
PARTITIONS 3, sizes 20142/19341/22156 bytes across the 3 partitions
  (150 messages spread across partitions, confirming end-to-end delivery)

$ uv run python -c "OpenSkyClient(...).​_ensure_token()"
token: None authenticated: False poll_interval: 60
  (confirms anonymous fallback works with no OPENSKY_CLIENT_ID/SECRET set)
```

## Schema freeze (pre-P3, 2026-07-28) ✅

Before building Spark schemas on top of assumptions, froze the real OpenSky
contract against two independent live captures:

- `tests/fixtures/opensky_real_sample.json` — Europe bbox, 1234 states.
- `tests/fixtures/opensky_real_sample_us.json` — continental US bbox, 879
  states (different region/time, to avoid mistaking "happened to be
  non-null once" for a guaranteed contract).
- `ingestion/schemas/opensky_raw.py` — new `OpenSkyStateVector`, derived
  field-by-field from the captures (exact positional order, types,
  nullability documented per field). `opensky.py` now parses live rows
  through this model instead of a hand-rolled `dict(zip(...))`.
- `test_non_optional_fields_are_never_null_in_any_real_fixture` checks both
  captures against every field `FlightState` marks required — no widening
  was needed (all "always non-null" fields were already `Optional` in the
  model out of defensiveness).
- `test_at_least_one_fixture_has_on_ground_aircraft` confirms `on_ground` is
  exercised: Europe capture had 155/1234 on the ground, US had 93/879.
- Callsign handling verified explicit: OpenSky right-pads to 8 chars
  (`"DLH9LF  "`) and sometimes returns `""` for craft with no filed
  callsign — both normalise through `FlightState`'s `strip_callsign`
  validator to a trimmed string or `None`, never `""`. Covered by 5
  parametrized cases in `test_callsign_normalisation`.
- `tests/test_schema_contract.py` (44 cases, both fixtures parametrized)
  proves simulator output and real-capture output validate through the same
  `FlightState` model with matching field names, types, and nullability.
  Sanity-checked it's not vacuous by injecting a fake type drift
  (`velocity` as `str`) into a throwaway test copy — it failed loudly with
  a clear diff, then the throwaway file was deleted.
- Added `ingestion/replay.py` (`ReplaySource`) + `--mode replay
  [--fixture-path ...]` on the producer, so a saved real capture can stream
  through the exact same producer/DLQ pipeline as simulate/opensky modes —
  verified live: 1234 real states replayed into `flights.raw` with 0 DLQ,
  tagged `source: "opensky"`.

**Verification performed:**
```
$ uv run ruff check .          -> All checks passed!
$ uv run pytest -q             -> 50 passed in 0.23s

$ uv run python -m ingestion.producer --mode replay --once
Loaded 1234 states from replay fixture tests/fixtures/opensky_real_sample.json
Published batch: sent=1234 dead_lettered=0 total_sent=1234 total_dlq=0

$ docker exec liveflights-redpanda rpk topic consume flights.raw -n 5 -o :end --offset -5
4082e7 opensky United Kingdom
4598c6 opensky Denmark
4b17fb opensky Switzerland
...
```

## P3 — Streaming (Spark bronze/silver/gold on Delta/MinIO) ✅ (2026-07-28)

**Version pinning** (the brief flagged this as the #1 place to lose hours —
pinned as a compatible set, verified by actually running it, not just
reading compatibility tables):
- PySpark `3.5.3`, bundling Hadoop client `3.3.4`
- `delta-spark==3.2.1` (Delta 3.x line for Spark 3.5)
- `spark-sql-kafka-0-10_2.12:3.5.3` — pinned to the exact Spark/Scala build
- `hadoop-aws:3.3.4` — matches the Hadoop version bundled inside PySpark 3.5.3
  (confirmed via `find .venv -name "hadoop-client-api*"`)
- `aws-java-sdk-bundle:1.12.262` — the SDK version hadoop-aws 3.3.4 itself
  depends on
- All packages resolved via Maven Central through `spark.jars.packages`;
  Ivy caches at `~/.ivy2` on the host, so restarts don't re-download (proved:
  second run of the same session showed `0 artifacts copied, 19 already
  retrieved`).
- Decided to run Spark as local host processes (`local[*]` via `uv run`)
  rather than in a Docker container — avoids needing a custom arm64 Spark
  image, and the Ivy/jar cache persists trivially on the host filesystem
  without any volume-mount ceremony. All three jobs still start from a
  single `uv run --group streaming python -m streaming.jobs.<name>`.
- `streaming/session.py` — single `get_spark_session()` used by all three
  jobs; every S3A/Delta/Kafka config lives here once (endpoint, path-style
  access, `SimpleAWSCredentialsProvider`, SSL disabled, Delta SQL extensions
  + catalog, shuffle partitions=8, driver/executor memory=2g).

**Jobs built:**
- `streaming/jobs/bronze_stream.py` — Kafka(`flights.raw`) -> Parquet at
  `s3a://liveflights/bronze/`. Raw JSON string + kafka partition/offset/
  timestamp + a `source_mode` tag, partitioned by `ingest_date`/`ingest_hour`
  derived from the Kafka broker's own record timestamp (no payload parsing
  in bronze at all). `processingTime="30 seconds"`, checkpoint at
  `./data/checkpoints/bronze`.
- `streaming/jobs/silver_stream.py` — bronze Parquet -> Delta at
  `s3a://liveflights/silver/`. Parses `raw_payload` against the explicit
  `FLIGHT_STATE_SCHEMA` StructType (never inferred). Enriches with
  `speed_kmh`, `altitude_ft` (baro, falls back to geo), `flight_phase`
  (ground/climb/cruise/descent), `geohash5` (via `pygeohash`), `region`
  (coarse lat/lon bounding boxes), `data_quality_flags` (array, thresholds
  matched to the simulator's injected anomaly types). Idempotency comes from
  a **Delta MERGE** in `foreachBatch`, keyed on `(icao24, time_position)`
  with null-safe equality — not from streaming `dropDuplicates` state alone,
  since that's only bounded by the watermark window.
- `streaming/jobs/gold_batch.py` — plain **batch** job (not streaming) over
  the silver Delta table, per the brief's guidance that streaming
  aggregations would force unneeded watermark/output-mode complexity here.
  Builds `traffic_by_hour`, `traffic_by_country`, `airline_activity`
  (static ICAO callsign-prefix lookup, `streaming/utils/airlines.py`),
  `altitude_band_distribution`, and `anomaly_events` (placeholder scoring —
  flags any row with non-empty `data_quality_flags`, real ML scoring lands
  in P5). Each table: full-overwrite write to Delta gold, then a single
  JDBC batch write (`batchsize=1000`) to Postgres — not row-by-row.

**Region bucketing test (mandatory per the brief):**
`tests/test_region_bucketing.py` validates `region_bucket()` against both
real fixtures from the P2 schema-freeze step: >=90% of the Europe-bbox
fixture buckets to `"Europe"`, >=90% of the US-bbox fixture buckets to
`"North America"`. Both passed on the first correct implementation.

**Blockers hit & resolved:**
1. `data_quality_flags()` was originally defined with keyword-only
   arguments — Spark UDFs always call the wrapped function positionally, so
   every row crashed the silver job with `TypeError: ... takes 0 positional
   arguments but 8 were given`. Fixed by making the signature positional;
   caught via reading the actual stack trace from the killed job's log
   rather than guessing.
2. **Real host port conflict, not a code bug**: a pre-existing Homebrew
   `postgresql@17` service (running since before this session, unrelated to
   this project) was bound to `127.0.0.1:5432` and `[::1]:5432`, silently
   swallowing all `localhost:5432` connections meant for the Dockerized
   Postgres — `gold_batch.py`'s JDBC write failed with `role "liveflights"
   does not exist` even though that role exists fine inside the Docker
   container (`docker exec ... psql` worked; JDBC from the host didn't).
   Fixed by remapping the Docker Postgres to host port **5433** in
   `docker-compose.yml`, `.env`, and `.env.example` (documented inline in
   `.env.example`). The user later confirmed the native Postgres could be
   stopped entirely (`brew services stop postgresql@17`); the project stays
   on 5433 regardless since it's already verified working end-to-end.
3. `silver_stream.py` appeared to stall (0% CPU, no progress) for several
   minutes after processing the first replay — root-caused as the local
   machine running 3+ concurrent `local[*]` Spark drivers (producer,
   bronze, silver, plus ad-hoc verification queries) competing for cores,
   not a logic bug; thread-dumping the JVM (`jstack`) showed the stream
   thread was healthy but simply slow to get scheduled. **Fixed properly**:
   replaced `local[*]` with a configurable `local[{SPARK_CORES}]` (default
   2) in `get_spark_session()` — this codebase always expects several Spark
   drivers to coexist (bronze + silver + gold + later Airflow tasks), so
   `local[*]` per-driver was always going to cause contention, not just a
   one-off fluke. `SPARK_CORES` now lives in `.env`/`.env.example` alongside
   the other streaming tunables (`SHUFFLE_PARTITIONS`, `DRIVER_MEMORY`,
   `EXECUTOR_MEMORY`, `CHECKPOINT_ROOT`), which were previously only
   defaulted in code and not exposed — fixed to keep `.env.example` the
   single source of truth per project rules.

**Restart-safety proof, made unambiguous** (identical before/after row
counts alone are consistent with either "restarted and correctly
processed" or "restarted but silently did nothing" — resolved by testing
with deliberately new, uniquely-tagged data instead of relying on
already-processed replay data):
```
# Baseline (after switching to local[2]): 9184 rows, Delta version 14 (15 commits)
BASELINE_TOTAL: 9184
BASELINE_VERSION_COUNT: 15   BASELINE_LATEST_VERSION: 14

# Published 5 brand-new records (icao24 = dead01..dead05, never seen before)
# then killed silver_stream (kill -9) IMMEDIATELY, before it could process them
$ uv run python scripts/publish_test_batch.py
Published 5 test records: ['dead01', 'dead02', 'dead03', 'dead04', 'dead05']
$ pkill -9 -f streaming.jobs.silver_stream

# Confirmed the crash genuinely happened BEFORE processing:
TEST_ROWS_BEFORE_RESTART: 0
TOTAL_BEFORE_RESTART: 9184

# Restarted silver_stream from checkpoint
$ uv run --group streaming python -m streaming.jobs.silver_stream
2026-07-28 14:50:27 INFO streaming.silver batch 17: merged 5 rows into silver
  -> merged EXACTLY 5 rows: the precise size of the post-crash test batch,
     not a round number that could be coincidental.

# Post-restart Delta table state:
TOTAL_AFTER_RESTART: 9189                    (9184 + 5, exactly)
TEST_ROWS_AFTER_RESTART: 5
 icao24  callsign  ingest_ts
 dead01  TEST001   2026-07-28 09:19:12.431   (all 5 present, correct content)
 dead02  TEST002   2026-07-28 09:19:12.432
 dead03  TEST003   2026-07-28 09:19:12.432
 dead04  TEST004   2026-07-28 09:19:12.432
 dead05  TEST005   2026-07-28 09:19:12.432

VERSION_COUNT_AFTER: 16                      (15 -> 16, one new commit)
 version=15  timestamp=2026-07-28 09:20:26  operation=MERGE
   -> new commit timestamped AFTER the restart (14:50 IST = 09:20 UTC),
      well after the last pre-crash commit (version 14 @ 08:49:33 UTC).
```
All three lines of evidence (exact row-count delta, exact content match,
new Delta commit timestamped after the restart) agree — this was genuine
correct reprocessing from checkpoint, not a stall that happened to leave
counts unchanged.

**Verification performed (all executed live, not claimed):**
```
# Version/jar resolution smoke test
$ uv run --group streaming python -c "get_spark_session(...); spark.range(5).show()"
Spark version: 3.5.3   (all 19 jars resolved from Maven Central, cached in ~/.ivy2)

# S3A/MinIO smoke test
$ ... spark.range(10).write.parquet('s3a://liveflights/_smoke_test/'); read back count: 10

# Producer ran continuously in simulate mode (~15 min total across the
# session, well past the requested ~3 minutes) while bronze_stream and
# silver_stream ran concurrently.

# Row counts across all three layers
BRONZE row count: 11652    (Parquet, partitioned ingest_date/ingest_hour)
SILVER row count: 9184     (Delta, deduped)
SILVER distinct (icao24,time_position): 9184   <- exactly equal, zero dupes
GOLD anomaly_events (Delta): 170 rows

# gold_batch.py output (Postgres, via psql)
gold.traffic_by_hour            2 rows
gold.traffic_by_country        53 rows
gold.airline_activity          23 rows
gold.altitude_band_distribution 8 rows
gold.anomaly_events            170 rows

SELECT * FROM gold.traffic_by_hour LIMIT 5;
 hour_bucket           | flight_count | avg_altitude_ft | avg_speed_kmh
 2026-07-28 12:30:00   | 150          | 31441.5         | 813.8
 2026-07-28 13:30:00   | 1395         | 30263.8         | 775.5

SELECT * FROM gold.traffic_by_country ORDER BY flight_count DESC LIMIT 5;
 Germany 323, Switzerland 105, United Kingdom 97, Malta 94, Austria 88

# Dedup proof: replayed the same 1234-record real fixture TWICE
$ uv run python -m ingestion.producer --mode replay --once   # 1st time
$ uv run python -m ingestion.producer --mode replay --once   # 2nd time, identical data
SILVER opensky-sourced row count after BOTH replays: 1234   (not 2468)
  -> Delta MERGE updated existing rows on (icao24, time_position) instead
     of inserting duplicates.

# Restart-safety proof: killed silver_stream mid-run (kill -9), restarted it
PRE_RESTART_TOTAL: 9184   PRE_RESTART_DISTINCT: 9184
  (kill -9 69733 69734)
POST_RESTART_TOTAL: 9184  POST_RESTART_DISTINCT: 9184   OPENSKY_ROWS: 1234
  -> identical counts before/after an unclean kill + restart from checkpoint:
     no data loss, no duplicates introduced.

# MinIO object listing, all three layers
bronze: 90 partitioned Parquet files under ingest_date=2026-07-28/ingest_hour={07,08}/
silver: Delta table, 15 _delta_log commits + 1 checkpoint.parquet, 8 partitions of data files
gold:   5 Delta tables (traffic_by_hour, traffic_by_country, airline_activity,
        altitude_band_distribution, anomaly_events), each with its own _delta_log

$ uv run ruff check .   -> All checks passed!
$ uv run pytest -q      -> 54 passed in 0.24s   (50 from P2 + 4 region-bucketing)
```

## P3 fixes before P4 ✅ (2026-07-28)

Two follow-ups requested before moving on:

1. **Resolved the restart-proof ambiguity.** Identical before/after row
   counts alone don't distinguish "restarted and correctly deduped" from
   "restarted but stalled and processed nothing." Proved it was the former
   with three independent signals: published 5 brand-new, uniquely-tagged
   records (`icao24 = dead01..dead05`, never seen before), killed
   `silver_stream` immediately (before it could process them, confirmed via
   a direct count showing 0 test rows present), restarted it, and showed
   (a) the log line `batch 17: merged 5 rows` — exactly the test batch
   size, (b) the Delta table content is exactly those 5 rows with
   post-restart timestamps, (c) Delta version history went 15 -> 16 with
   the new commit timestamped *after* the restart. All three agree.
2. **Fixed the resource contention properly, not just worked around it.**
   `get_spark_session()` now takes `local[{SPARK_CORES}]` instead of
   `local[*]` (`SPARK_CORES` env var, default 2) — this codebase always
   expects multiple concurrent Spark drivers (bronze + silver + gold, and
   Airflow tasks from P8 onward), so `local[*]` per-driver was always going
   to cause scheduling contention, not a one-off fluke. Also backfilled
   `SPARK_CORES`, `SHUFFLE_PARTITIONS`, `DRIVER_MEMORY`, `EXECUTOR_MEMORY`,
   `CHECKPOINT_ROOT` into `.env`/`.env.example`, which had been defaulted in
   code only — a gap in the "every config field maps to .env.example" rule.

Full before/after counts and the exact commands run are in the P3 section
above (see "Restart-safety proof, made unambiguous").

## P4 — dbt ✅ (2026-07-28)

`transform/` — dbt-core 1.8.9 + dbt-postgres 1.8.2, project profile
`liveflights`, `profiles.yml` kept in-repo (not `~/.dbt/`) so the project is
self-contained; connection is entirely `env_var()`-driven (host/port/db have
non-secret defaults matching `.env`, user/password have no default —
required from the environment, never hardcoded).

- **Postgres port**: `profiles.yml` targets host port **5433** (not 5432),
  documented inline — same reasoning as the P3 fix (a pre-existing native
  Postgres on this machine occupies 5432).
- **`analytics` schema** created in Postgres for marts (`staging`/`gold`
  schemas already existed from P1). Added `macros/generate_schema_name.sql`
  overriding dbt's default schema-prefixing behavior so custom `+schema`
  configs produce exactly `staging`/`analytics`, not `analytics_staging`.
- **`sources.yml`**: all 5 gold tables, with freshness checks on the two
  that have a meaningful timestamp column (`traffic_by_hour.hour_bucket`,
  `anomaly_events.ingest_ts`; the other three are keyed rollups with no
  natural "loaded at" column).
- **Staging** (5 views): `stg_traffic_by_hour`, `stg_traffic_by_country`,
  `stg_airline_activity`, `stg_altitude_band_distribution`,
  `stg_anomaly_events` — rename/cast/trim only, no new business logic.
- **Intermediate** (2 views): `int_dim_airline`, `int_dim_country` (the
  latter enriched with a coarse `region` bucket via the
  `country_to_region()` macro — business-level reporting grouping, not a
  re-derivation of the lat/lon regioning Spark already does in silver).
  Both use `generate_surrogate_key()` (a small local macro equivalent to
  `dbt_utils.generate_surrogate_key`, kept in-repo to avoid an external
  package dependency for one macro) — three macros used across models in
  total (`generate_surrogate_key`, `country_to_region`, `pct_change`).
- **Marts** (4 tables): `mart_traffic_daily` (daily rollup + day-over-day
  `pct_change()`), `mart_region_summary`, `mart_airline_leaderboard`
  (ranked via `row_number()`), `mart_anomaly_summary` (by type/region/hour).
- **Tests**: 47 total — `not_null`/`unique` across staging/intermediate/
  marts, `accepted_values` on `altitude_band` and `region`, a
  `relationships` test (`mart_airline_leaderboard.airline_key` ->
  `int_dim_airline`), and the required singular test
  `tests/assert_no_negative_flight_counts.sql` (unions all four
  flight_count-bearing staging models).
- **Snapshot**: `snapshots/airline_activity_snapshot.sql` over
  `source('gold', 'airline_activity')`, `check` strategy on
  `flight_count`/`avg_speed_kmh`/`avg_altitude_ft`, `unique_key=airline`.
- Makefile: `dbt-debug`, `dbt-run`, `dbt-test`, `dbt-snapshot`, `dbt-docs`
  targets (each sources `.env` then invokes `uv run --group dbt dbt ...`
  with `--project-dir transform --profiles-dir transform`).
- Also fixed `make test` itself: it was only running `uv run pytest`
  without the `streaming` dependency group, so `test_region_bucketing.py`
  (which imports `pygeohash` via `streaming/utils/enrich.py`) would fail to
  collect on a fresh `uv sync --group dbt`. Now runs
  `uv run --group streaming --group dbt pytest -q`.

**Verification performed (all executed live):**
```
$ make dbt-debug   -> Connection test: OK connection ok

$ make dbt-run
Found 11 models, 1 snapshot, 47 data tests, 5 sources, 437 macros
Finished running 7 view models, 4 table models in 0.44s
Done. PASS=11 WARN=0 ERROR=0 SKIP=0 TOTAL=11

$ make dbt-test
Finished running 47 data tests in 0.86s
Done. PASS=47 WARN=0 ERROR=0 SKIP=0 TOTAL=47
  (confirmed accepted_values_int_dim_country_region and
   accepted_values_stg_altitude_band_distribution_altitude_band both ran
   and passed; confirmed assert_no_negative_flight_counts ran and passed)

$ make dbt-snapshot
1 of 1 OK snapshotted analytics.airline_activity_snapshot [SELECT 23]

$ make dbt-docs
Catalog written to transform/target/catalog.json

$ psql ... -c "\dt analytics.*"
 airline_activity_snapshot | table
 mart_airline_leaderboard  | table
 mart_anomaly_summary      | table
 mart_region_summary       | table
 mart_traffic_daily        | table

SELECT * FROM analytics.mart_traffic_daily LIMIT 5;
 traffic_date | total_flights | avg_altitude_ft | avg_speed_kmh | prior_day_flights | day_over_day_change_pct
 2026-07-28   | 1545          | 30852.7         | 794.6         | (null, only 1 day of data so far)

SELECT * FROM analytics.mart_airline_leaderboard LIMIT 5;
 Unknown/Other 1129, Ryanair 100, Lufthansa 71, Wizz Air 34, Austrian Airlines 28
 (each with a stable md5 surrogate airline_key and activity_rank)

$ make test   -> ruff: All checks passed!   pytest: 54 passed in 0.25s
```

## P5 — ML ✅ (2026-07-28)

**Revised scope, and why.** The original plan called for a point-wise
IsolationForest anomaly detector. Silver's `data_quality_flags` (rule-based
thresholds: implausible speed/altitude/vertical-rate, missing position,
emergency squawk) already catch every physically-impossible state a
point-wise model would learn to flag — training an IsolationForest on the
same features would just rediscover those thresholds with extra steps and
zero interpretability gained. Rejected as not defensible.

**Division of labour** (documented here and in README.md):
**RULES catch physically impossible. ML catches contextually unusual.**
A flight state can be perfectly plausible in isolation (legal speed, legal
altitude) and still be behaviorally strange — off the corridor everyone
else on that route flies, against the flow of traffic, at an altitude
nobody else uses on that path. Rules structurally cannot see that, because
"context" isn't a per-row threshold — it has to be learned from the
population. That's the actual job for ML here, and it drove every model
below.

### Model 1 — Air corridor discovery (DBSCAN)

`ml/corridors.py`. Features: scaled `latitude`, `longitude`,
`sin(true_track)`, `cos(true_track)` — heading included deliberately so
opposite-direction traffic on the same lat/lon airway separates into
distinct corridors instead of merging. Filtered to `on_ground=false`,
`flight_phase='cruise'`.

- `eps` chosen via a k-distance elbow (k=`min_samples`=8), knee found by
  max perpendicular distance from the line joining the sorted-distance
  curve's endpoints (no extra dependency).
- Output: `gold.corridor_assignments` (per-point `corridor_id`) and
  `gold.flight_corridors` (centroid, modal heading via circular mean,
  altitude p10/p50/p90, member count, a 10-point simplified polyline for
  map rendering, projected along the corridor's own heading).

**Result (real run, 15,000 cruise/airborne rows):** eps=0.0490,
**238 corridors**, **5.2% noise**, **silhouette=0.607**. Top 5 by member
count: corridor 93 (centroid 50.69,10.04, heading 254°, alt p50 34,435ft,
243 members), corridor 117 (45.88,-0.25, heading 25°, 192 members),
corridor 53 (47.19,6.58, heading 316°, 191 members), corridor 79
(44.22,1.44, heading 351°, 177 members), corridor 6 (59.94,14.80, heading
279°, 173 members).

### Model 2 — Trajectory prediction (GradientBoostingRegressor, deltas)

`ml/trajectory.py`. Per-icao24, time-ordered sequences; features: current
lat/lon/velocity/true_track (sin+cos)/vertical_rate/baro_altitude, plus
turn-rate, acceleration, and climb-trend computed over the last
observation (turn rate via atan2 of the heading-vector rotation, to avoid
the 359°→1° wraparound bug a naive subtraction has). Target: `delta_lat`,
`delta_lon` 5 minutes ahead — **predicted as deltas, not absolute
coordinates**. Valid pairs found via a per-icao24 `merge_asof` (nearest
match to t+300s within a 30s tolerance).

- **Pair count check (mandatory before training):** 11,459 valid pairs —
  well above the 2,000 minimum, so training proceeded.
- **Time-based 80/20 split** (sorted by `time_position`; never random —
  consecutive observations of the same aircraft are highly correlated and
  would leak across a random split).
- **Mandatory baseline**: dead reckoning — great-circle destination-point
  projection along current track/speed for 300s.
- Metric: haversine error, median + p90 km, **both model and baseline**,
  broken down by phase (`turning` = |turn_rate| > 1°/s, overriding
  cruise/climb/descent for that row).

**Result (real run, test n=2292):**
```
  phase    n  model_median_km  model_p90_km  dr_median_km  dr_p90_km
 cruise 2106            2.455         5.118         9.300     15.280
  climb  103            2.269         4.626        10.920     15.806
descent   83            1.760         4.897         9.482     14.693
OVERALL 2292            2.422         5.109         9.425     15.303
```
Model beats dead reckoning in every phase present, including cruise
(2.4km vs 9.4km median) — an honest result, not the "cruise favors dead
reckoning" outcome we expected going in. Best guess why: the simulator's
cruise segments aren't perfectly straight-line (small velocity/heading
noise + gradual convergence toward the next waypoint), so turn-rate/
acceleration features give the model real signal a constant-heading
projection can't use. **Caveat, stated plainly**: zero rows landed in the
`turning` bucket this run — the simulator's per-tick heading noise never
exceeds the 1°/s sustained-turn threshold, so this breakdown is honest but
untested against a real maneuvering segment; would need either a lower
threshold or a longer collection window with more route-transition points
to populate it. Registered as `trajectory-predictor-lat` /
`trajectory-predictor-lon`, promoted to Production (no prior version to
compare against). Predictions for the test set written to
`gold.trajectory_predictions` for the frontend's ghost-trail overlay.

### Model 3 — Contextual anomaly detection (built on Model 1)

`ml/anomaly.py`. Per cruise/airborne point: lateral distance (haversine)
to its nearest corridor centroid, heading deviation from that corridor's
modal heading, altitude z-score against that corridor's own member
mean/std, and a noise flag for DBSCAN-unassigned points. Combined into
`anomaly_score` (equal-weighted 0.25 each, each component clipped to
[0,1] against a fixed scale — documented in the module, not tuned against
a held-out set given the time budget).

**Overlap table (real run, 17,162 scored points)** — the actual evidence
this adds signal beyond the rules:
```
    bucket  count
rules_only    161
   ml_only   3291
      both     50
   neither  13660
     total  17162
```
3,291 states get flagged by the contextual model that the rule thresholds
never would — that's the headline result. **Caveat**: at the current
threshold (0.5) the ML-only rate (~19% of all scored points) is high for
a production anomaly detector; reasonable next step would be tuning the
threshold or component weights against labeled data, which we don't have.
Sanity check only (explicitly not the headline metric — the injected
anomalies are themselves rule-shaped, so agreement here is partly
circular): ML flags 23.7% of rule-flagged states. `gold.anomaly_events`
now carries score, contributing reasons (rule flags + ML reasons,
comma-joined), and corridor context (`nearest_corridor_id`,
`lateral_distance_km`, `heading_deviation_deg`, `altitude_z`,
`unassigned_corridor`).

### Model 4 — Traffic forecast (GradientBoostingRegressor, kept small)

`ml/forecast.py`. Real accumulated `stg_traffic_by_hour` history is only a
couple of hours long — nowhere near enough for a `lag_24` feature — so a
**7-day SYNTHETIC hourly history** (diurnal + mild weekday/weekend pattern
+ noise) is generated and labeled `is_synthetic=True`/logged as
`data_source: SYNTHETIC` everywhere it appears, never presented as real
traffic. Features: `lag_1/2/3/24`, cyclical hour encoding, day-of-week.
Time-based split (last 20% of hours, no shuffling).

**Result (real run, synthetic data, test n=29 hours):**
```
                                    mae       rmse      mape
model                          8.506      10.814     0.1057
baseline_last_value           12.759      14.835     0.1616
baseline_same_hour_yesterday  13.345      16.544     0.1496
```
Model beats both naive baselines on every metric. Registered as
`traffic-forecaster`, promoted to Production. A demo recursive 6-hour
forecast from the synthetic series' tail is also logged (values ~100-140,
consistent with the synthetic diurnal curve — not a real prediction of
actual traffic, since the underlying series is synthetic).

### MLflow

Four separate experiments (`flight-corridors`, `trajectory-prediction`,
`contextual-anomaly-detection`, `traffic-forecast`), each run logging
params, all metrics, `mlflow.sklearn` model artifacts, feature-importance
plots (trajectory, forecast), and the k-distance elbow plot (corridors).
Models 2 and 4 registered to the Model Registry and promoted to Production
(both were first-ever versions, so promotion was unconditional — the
promote-only-on-improvement comparison logic in `_maybe_promote()` is in
place for the next retrain). `ml/registry.py` provides
`load_trajectory_models()` / `load_forecast_model()` with local-pickle
fallback (`ml/artifacts/*.pkl`, written by each training script) for P6.

**Blockers hit & resolved:**
1. **PySpark 3.5.3's `toPandas()` is broken on Python 3.12** —
   `from distutils.version import LooseVersion` raises
   `ModuleNotFoundError` (`distutils` was removed outright in 3.12, PEP
   632; setuptools' shim isn't installed by default in this uv venv
   either). Fixed by never calling `toPandas()`: `ml/data.py` writes the
   Spark DataFrame to a local temp Parquet dir, then reads it back with
   plain `pandas.read_parquet` (pyarrow-backed, unrelated code path) —
   sidesteps the broken conversion entirely rather than patching around it.
2. **MLflow client-side S3 artifact upload needs `boto3`**, even though
   the MLflow *server* already proxies most operations — direct artifact
   PUTs from the client go straight to S3/MinIO and need the SDK installed
   client-side. Added `boto3` to the `ml` dependency group, and exported
   `MLFLOW_S3_ENDPOINT_URL`/`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`
   (pointed at MinIO, not real AWS) when invoking the training scripts.
3. **`ml/anomaly.py`'s rewrite of `gold.anomaly_events` hit a real
   cross-phase dependency**: dbt's `stg_anomaly_events` view (P4) depends
   on the table, so pandas' `to_sql(if_exists='replace')` — which issues a
   bare `DROP TABLE` — failed with `DependentObjectsStillExist`. Fixed by
   explicitly `DROP TABLE ... CASCADE` first, then `to_sql(if_exists=
   'append')` against the now-absent table (`dbt run` recreates the
   dependent view on its next invocation). **Note for P8 orchestration**:
   the DAG must sequence `gold_batch.py` (rule-based baseline) before
   `ml/anomaly.py` (ML enrichment) — `ml/anomaly.py` reads rule flags
   straight from silver's `data_quality_flags`, not from
   `gold.anomaly_events`, so it has no hard ordering dependency on Spark's
   batch job, but running Spark's `gold_batch.py` afterward would
   overwrite the ML-enriched table back to rules-only.

**Verification performed (all executed live against the continuously-
running producer/bronze/silver jobs, ~30+ min of accumulated data):**
```
$ uv run --group ml --group streaming python -m ml.corridors
Cruise, airborne rows for corridor discovery: 15000
Chosen eps=0.0490 via k-distance knee (k=8)
corridors=238 noise_pct=5.2% silhouette=0.6065155731852495
Wrote gold.flight_corridors (238 corridors) and gold.corridor_assignments (15000 rows)

$ uv run --group ml --group streaming python -m ml.trajectory
Valid (t, t+5min) trajectory pairs found: 11459
[error table above]
Overall: model beats dead reckoning (median 2.422km vs 9.425km)
Promoted trajectory-predictor-lat v1 to Production (no prior Production model)
Promoted trajectory-predictor-lon v1 to Production (no prior Production model)
Wrote 2292 rows to gold.trajectory_predictions

$ uv run --group ml --group streaming python -m ml.anomaly
[overlap table above]
Wrote 3502 rows to gold.anomaly_events (rule/ML-flagged, with corridor context)

$ uv run --group ml --group streaming python -m ml.forecast
[forecast table above]
Model beats both naive baselines on MAE.
Promoted traffic-forecaster v1 to Production (no prior Production model)

$ psql ... -c "SELECT ... FROM gold.flight_corridors ORDER BY member_count DESC LIMIT 5"
[shown above]
$ psql ... -c "SELECT ... FROM gold.anomaly_events ORDER BY anomaly_score DESC LIMIT 5"
[shown above, all top-5 are unassigned_corridor=true, score=1.0 — the noise/no-corridor case]

$ psql ... row counts: flight_corridors=238, corridor_assignments=15000,
  anomaly_events=3502, trajectory_predictions=2292

MLflow run listing (via MlflowClient.search_runs, one FINISHED run per
experiment except flight-corridors which also has one earlier FAILED run
from before the boto3 fix — left as-is, real history):
  flight-corridors: 238 corridors, noise=5.2%, silhouette=0.607
  trajectory-prediction: cruise_model_median_km=2.455, ...
  contextual-anomaly-detection: overlap_rules_only=161, overlap_ml_only=3291, ...
  traffic-forecast: model_mae=8.506, model_rmse=10.814, model_mape=0.106

$ uv run ruff check .   -> All checks passed!
```

Producer + bronze_stream + silver_stream were left running throughout this
entire phase (per instruction) and are still running.

## P5 verification checks (pre-P6) ✅ (2026-07-28)

Three checks requested before moving on, all executed for real:

**CHECK 1 — dbt regression from the anomaly_events CASCADE drop.**
Re-ran `dbt run` (11 models, all OK) and `dbt test` (47/47 PASS) — the
CASCADE drop only removed the dependent `stg_anomaly_events` view, which
dbt recreated on the next `dbt run` as expected. No regression.

**CHECK 2 — trajectory baseline diagnosis.** A 4x model win over dead
reckoning in cruise was flagged as implausible. Investigated three ways:
- **Actual pair time-gap distribution**: min=270s, p10-p90=300-301s,
  mean=299.5s, max=301s — tightly clustered at the nominal 300s, so the
  fixed-horizon assumption wasn't unfairly penalizing the baseline.
- **Manual numeric check of the destination-point formula**: 100m/s due
  east for 300s from the equator gives delta_lon=0.269796°; expected value
  from first principles (30km / 111.32km-per-degree at the equator) is
  0.2695° — matches to 3 decimal places. No unit or radians/degrees bug.
- **Fixed anyway** (more physically correct regardless): dead reckoning
  now uses each pair's *actual* elapsed seconds
  (`future_time_position - time_position`), not a hardcoded 300.
- **Re-ran with the fix**: model still beats dead reckoning overall
  (median 2.489km vs 9.004km) and in cruise specifically (2.520km vs
  8.972km) — the 4x gap holds after removing every suspected confound.
- **Real (non-buggy) explanation, found and stated plainly**: the
  simulator injects independent random noise into *every single reading*
  — `velocity = CRUISE_SPEED_MPS + uniform(-15, 15)` m/s and
  `vertical_rate = uniform(-0.5, 0.5)` each tick
  (`ingestion/simulator.py`) — while the *true* underlying motion is a
  deterministic great-circle at constant speed. Dead reckoning extrapolates
  from one noisy instantaneous sample as if it were the average velocity
  for the next 5 minutes; the GBR model, trained across thousands of
  examples, learns to regress toward the true expected speed and
  effectively denoises the per-tick jitter. This is a real, defensible
  reason a learned model beats a physics baseline here — not a bug.
- **Per-source breakdown** (requested explicitly): **100% of the 21,969
  valid pairs are `simulate`-sourced; zero are `opensky`-sourced.** Stated
  plainly per instruction: the real OpenSky capture only exists as a
  single point-in-time replayed snapshot (one row per aircraft, no time
  series), so it can never form a valid (t, t+5min) pair. **This means
  Model 2 has only ever been evaluated against the simulator's own
  generative process, and the result above may reflect the model learning
  to denoise that specific generator rather than anything that would
  generalize to real flight dynamics.** Documented in README.md as an
  explicit limitation, not glossed over.

**CHECK 3 — anomaly threshold recalibration.** ~27% of cruise points were
flagged at the original threshold (0.5) — not a defensible "anomaly" rate.
- **Score percentiles** (26,697-point run): p50=0.359, p75=0.503,
  p90=0.575, p95=0.623, p96=0.634, **p97=0.653**, p98=0.784, p99=0.936,
  p99.5=1.0.
- **Calibrated threshold: 0.65** (≈p97) — chosen because it's the lowest
  round threshold landing inside the requested 2-5% band; swept
  0.5→7.2%, 0.6→7.2%, 0.65→3.1%, 0.7→2.6%, 0.75→2.4%, 0.8→2.0%, picked
  0.65 for the middle of the band rather than its edge.
- **Recalibrated overlap** (26,697 points): `rules_only=285, ml_only=780,
  both=39, neither=25593` — flagged rate 3.07%, in-band.
- **The actual proof, not just counts** — top 5 ML-only detections by
  score, with real field values:
  - `icao24=dead03`: lat/lon (53.0, 13.0), heading 90° vs corridor modal
    254°ish (53.5° deviation), altitude 29,528ft vs corridor mean
    34,449ft (**z=-52.9**, extreme), unassigned to any corridor, score
    1.0. **This is one of the deliberately-injected test records from the
    P3 restart-safety proof** (`icao24 = dead01..dead05`, still present in
    silver) — a fabricated record with no relation to any real flight
    route, correctly identified as maximally anomalous. Good corroborating
    evidence the detector isn't just flagging noise.
  - Four more real simulator records, all `unassigned_corridor=True`,
    with heading deviations of 131.7°, 45.2°, 57.0°, and 98.4° and
    altitude z-scores of -5.7, -6.1, +35.2, and -291.0 — genuinely extreme
    values by any reasonable definition, not borderline noise the
    threshold happened to catch.

**Verification commands run:**
```
$ dbt run --project-dir transform --profiles-dir transform  -> PASS=11
$ dbt test --project-dir transform --profiles-dir transform -> PASS=47

$ uv run --group ml --group streaming python -m ml.trajectory
Actual pair time gaps (s): min=270.0 p10=300.0 median=300.0 p90=301.0 max=301.0 mean=299.5
Pairs by source: {'simulate': 21969}
Trajectory error by source: simulate/OVERALL n=4394 model_median=2.489 dr_median=9.004
Trajectory error by phase:  cruise n=4143 model_median=2.520 dr_median=8.972
                             climb  n=38   model_median=3.672 dr_median=11.028
                             descent n=213 model_median=2.162 dr_median=9.129
Kept existing Production model (2.422km <= new 2.489km)  # v1 stays Production

$ uv run --group ml --group streaming python -m ml.anomaly
anomaly_score percentiles: p50=0.3627 ... p97=0.6525 ... p99.5=1.0000
At threshold=0.65: flagged rate=3.07% (target band: 2-5%)
Rules vs ML anomaly overlap: rules_only=285 ml_only=780 both=39 neither=25593
Top 5 ML-only detections: [shown above]
Wrote 1104 rows to gold.anomaly_events

$ dbt run (re-verified after the recalibrated anomaly_events rewrite) -> PASS=11
$ psql ... flight_corridors=238, anomaly_events=1104, trajectory_predictions=4394
```

## P6 — API ✅ (2026-07-28)

`api/` — FastAPI + Pydantic v2, all config via `pydantic-settings` from
`.env` (`api/config.py`). No hardcoded hosts/ports/secrets.

**Architecture decisions:**
- **Live positions never lived in Postgres or Delta** for the "hot path" —
  querying Spark/Delta per HTTP request would be far too slow. Instead
  `api/services/live_store.py` runs a background thread with its own
  Kafka consumer (fresh, unique `group.id`, `auto.offset.reset=latest`)
  that maintains an in-memory dict of the latest state + a short history
  per aircraft. `/api/flights/live` and `/ws/flights` both read from this;
  `/api/flights/{icao24}/trajectory` uses the short history to compute the
  same lag features (turn rate, acceleration, climb trend) Model 2 trains
  on, from just the last 2 observations.
- Plain synchronous SQLAlchemy + psycopg2 for Postgres (`api/deps/db.py`)
  — async drivers add complexity this data volume (low thousands of rows)
  doesn't need; FastAPI runs sync route bodies in a threadpool anyway.
- Redis caching (`api/deps/cache.py`) wraps data-fetch functions, not
  routes directly, and returns `(data, cache_hit)` so routers surface
  hit/miss via an `X-Cache` response header rather than polluting the
  JSON payload.
- Models 2 and 4 loaded once at startup via `ml/registry.py`
  (`api/services/models_loader.py`), tracking *which* path succeeded
  (`mlflow_registry` vs `local_pickle`) so `/health` can report it and the
  fallback can be verified end-to-end.

**Endpoints** (all with OpenAPI `summary`/`description`/`response_model`):
`GET /health`, `GET /api/flights/live`, `GET /api/flights/{icao24}/trajectory`,
`GET /api/stats/overview`, `GET /api/stats/traffic-by-hour`,
`GET /api/stats/by-country`, `GET /api/anomalies`,
`POST /api/predict/anomaly`, `GET /api/corridors`,
`GET /api/forecast/traffic`, `WS /ws/flights`.

**Cross-cutting:** CORS (`CORSMiddleware`), request-ID + structured JSON
access logging (`api/middleware.py` + `api/logging_config.py`), Prometheus
`/metrics` (`prometheus_client`, request-count + latency histogram per
route via `api/metrics.py`).

**Blocker hit & resolved: shared-venv group eviction killed a live Spark
job.** `uv sync --group X` (and `uv run --group X ...`) reconciles the
*entire* shared `.venv` to only the requested group(s), uninstalling
packages from every other group — including deleting PySpark's jar files
out from under the long-running `silver_stream` JVM process while it was
mid-flight, crashing it with `NoSuchFileException:
.../pyspark/jars/hadoop-client-api-3.3.4.jar`. Fixed the immediate problem
by restarting `silver_stream`, and fixed the root cause by syncing all
active groups together in one call (`uv sync --group streaming --group ml
--group dbt --group api`) instead of switching between exclusive
single-group syncs mid-session. **Lesson for future sessions**: when
multiple long-running processes from different dependency groups need to
coexist, sync every group they need at once, don't alternate.

**Verification performed (all executed live, against the real running
producer/bronze/silver/gold pipeline):**
```
$ curl /health
{"status": "ok", "database": {"ok": true}, "redis": {"ok": true},
 "kafka_live_store": {"ok": true},
 "trajectory_model": {"ok": true, "detail": "mlflow_registry"},
 "forecast_model": {"ok": true, "detail": "mlflow_registry"}}

$ curl /api/flights/live?limit=3        -> 3 real live aircraft, correct schema
$ curl /api/stats/overview               -> active_flights=152 countries=17
                                             avg_altitude_ft=30328.5 anomaly_count=1104
$ curl /api/stats/traffic-by-hour        -> 2 real hourly points, is_synthetic=false
$ curl /api/stats/by-country?limit=5    -> Germany 323, Switzerland 105, UK 97, Malta 94, Austria 88
$ curl /api/anomalies?page=1&page_size=2 -> 2 real events with full corridor context
$ curl /api/corridors?limit=2            -> top 2 corridors (243, 192 members) with 10-pt polylines
$ curl /api/flights/{icao24}/trajectory  -> 4-point recent track + predicted ghost point
                                             (computed live from the last 2 observations)
$ curl -X POST /api/predict/anomaly (plausible state)   -> is_anomaly=false, score=0.398
$ curl -X POST /api/predict/anomaly (implausible state) -> is_anomaly=true, score=1.0,
    rule_flags=[implausible_speed, implausible_altitude, implausible_vertical_rate,
                emergency_squawk], ml_reasons=[far_from_corridor, heading_deviation,
                altitude_outlier]
$ curl /api/forecast/traffic  -> trained_on_synthetic_history=true, 6 hourly points w/ bounds

# WebSocket: connected, received 3 frames ~3s apart
frame 1: count=154   frame 2: count=154   frame 3: count=155

# Redis cache proof (flushed first for a clean MISS)
1st request: X-Cache=MISS, time_total=16.2ms  (real Postgres query)
2nd request: X-Cache=HIT,  time_total=2.4ms   (~6.7x faster, identical body)

# Model fallback proof: stopped the MLflow container, restarted the API
$ docker compose stop mlflow && restart API
/health -> trajectory_model.detail="local_pickle", forecast_model.detail="local_pickle"
/api/forecast/traffic still returns real predictions (served from the pickle fallback)
$ docker compose start mlflow && restart API
/health -> trajectory_model.detail="mlflow_registry", forecast_model.detail="mlflow_registry"
   (both paths independently verified working, not just the fallback)

$ curl /metrics | head
# HELP liveflights_api_requests_total Total API requests
# TYPE liveflights_api_requests_total counter
liveflights_api_requests_total{method="GET",path="/health",status_code="200"} 1.0
...
Content-Type: text/plain; version=1.0.0; charset=utf-8   (valid Prometheus exposition format)

# p95 latency, 30 requests each, three heaviest endpoints
api/anomalies?page_size=50   n=30 p50=9.2ms  p95=45.0ms  max=101.6ms
api/corridors?limit=50       n=30 p50=8.5ms  p95=10.3ms  max=12.4ms
api/flights/live?limit=500   n=30 p50=1.8ms  p95=1.9ms   max=2.6ms

$ uv run ruff check .                                -> All checks passed!
$ uv run --group streaming --group dbt pytest -q     -> 54 passed
```

Producer + bronze_stream + silver_stream + the API server were all left
running at the end of this phase.

## P7 — Frontend ✅ (2026-07-28)

`web/` — Next.js 14 App Router, TypeScript, Tailwind v3, Leaflet
(react-leaflet), Recharts, pnpm. Design direction: an air-traffic-control
display — dark slate/navy, cyan/teal for normal traffic, amber for
warnings, red reserved for anomalies, monospace tabular numbers, no
decorative gradients or emoji.

**Stack pinning:** `pnpm create next-app@latest` resolved to Next 16 (not
the required Next 14), so `web/` was rebuilt by hand with an exact-pinned
`package.json`: Next 14.2.35, React 18.3.1, react-leaflet 4.2.1,
Tailwind 3.4.17, TypeScript 5.4.5, ESLint 8.57.1 — no `^` ranges, to avoid
the ecosystem resolving to Next 16 / React 19 / Tailwind v4 / ESLint v10.

**Architecture decisions:**
- Leaflet touches `window` at import time; `FlightMap` is loaded via
  `next/dynamic(() => import(...), { ssr: false })` so it never executes
  during SSR.
- `components/map/AircraftLayer.tsx` keeps an imperative
  `useRef<Map<string, L.Marker>>` marker registry rather than declarative
  `<Marker>` elements — at 150+ aircraft updating every 3s, recreating
  markers each tick visibly stutters. Existing markers get `setLatLng`,
  a CSS `rotate()` on the icon div, and a `path.setAttribute("fill", ...)`
  color update in place; only aircraft that actually disappear from the
  feed get `marker.remove()`.
- `hooks/useFlightsWebSocket.ts` reconnects with exponential backoff
  (500ms base, capped 15s) and exposes a status enum surfaced as a
  connection-status dot in the pipeline health strip.
- CARTO `dark_matter` tiles (no API key) for a dark basemap — default OSM
  tiles are light and would clash with the ATC theme.
- `lib/format.ts` `isSyntheticTestRecord()` (regex `/^dead0\d$/`) filters
  the ~5 artificial `dead0X` test records (P3 restart proof) out of the
  anomaly feed by default via an "include test records" checkbox — they
  remain queryable, just not shown by default, per the explicit
  requirement to keep the demo clean without hiding the data.
- Forecast segment (`TrafficForecastChart`) is rendered as a visually
  distinct dashed amber extension with a confidence band, labeled inline
  "forecast (model trained on synthetic history)" — data-honesty
  requirement: never let a synthetic-derived series look like observed
  traffic.
- Altitude histogram is computed client-side from the live WebSocket feed
  (no dedicated endpoint), reusing the same `ALTITUDE_BANDS` as the map
  legend so colors stay consistent across the whole UI.

**Bugs found and fixed during build/verification:**
- **All floating panels invisible** despite correct DOM/CSS: neither
  `<main>` nor the map wrapper established a CSS stacking context
  (`position` without a non-`auto` `z-index`), so Leaflet's internal panes
  (z-index up to 700) painted above the panels regardless of DOM order.
  Fixed with explicit `z-[1000]` on every floating panel wrapper.
- **Aircraft icons rendered as 4-pointed stars**: the original SVG path
  was a star/dart shape, not a plane. Replaced with a proper top-down
  aircraft silhouette in `AircraftLayer.tsx`.
- **Charts panel completely blank**: `.recharts-wrapper` resolved to
  ~41,000px tall — `ResponsiveContainer` needs a fully-resolved pixel
  height at every ancestor, and a percentage `calc()` chain through grid
  cells never resolved. Fixed by rewriting `ChartsPanel.tsx` to use
  explicit fixed pixel heights at every nesting level.
- **Layer-toggle panel overlapped the anomaly feed** after switching its
  toggles to a taller `flex-col` layout: two independently `top`-positioned
  absolute panels with a guessed pixel gap. Fixed by restructuring the
  right side into one real flex column.
- **Leaflet popups unstyled (default white Leaflet CSS)**: `leaflet.css`
  loads inside the dynamically-imported map chunk and can load after
  `globals.css`, winning on equal specificity. Fixed with `!important` on
  the `.leaflet-popup-*` dark-theme overrides.
- **React 18 StrictMode + react-leaflet incompatibility**: dev-only
  double-invoked effects threw "Map container is already initialized."
  Only affects `next dev`, not production builds. Fixed with
  `reactStrictMode: false` in `next.config.js`, documented inline as a
  dev-only workaround.
- **`next dev` chunks returning 503** after the machine had been idle for
  hours with several long-running Spark jobs restarted alongside it: the
  dev server process itself was in a wedged state (webpack chunks 503,
  page rendered with zero CSS). Fixed by killing and restarting the
  `next dev` process — not a code bug, a stale dev-server-process issue.

**Verification performed (live, against the running API/WS):**
```
$ pnpm build
✓ Compiled successfully
✓ Linting and checking validity of types ... (zero TypeScript errors)
Route (app)                              Size     First Load JS
┌ ○ /                                    111 kB          199 kB
└ ○ /_not-found                          876 B          88.7 kB

# Dev server against the live API: WS status "open", 545-557 active
# flights updating every ~3s, KPI cards live (active flights, countries,
# avg altitude, anomalies/hr), pipeline health strip all-green
# (Postgres/Redis/Kafka feed/trajectory model/forecast model).

# Corridor layer: 238 total corridors, top-20-by-member-count shown by
# default, slider confirmed adjustable up to 238.

# Anomaly feed: dead0X test records excluded by default (checkbox
# "include test records" unchecked), 1,104 total anomalies, real
# anomalies (VGP9612, MNV3537, YSN6110, ...) shown with score + reason
# codes + corridor context.

# Click-to-fly-to confirmed via network trace: clicking anomaly card
# fetched /api/flights/{icao24}/trajectory (verified 200 for icao24
# 2a077e) and the map flew to its position; distant anomalies (e.g. a
# Portugal-registered aircraft) moved the map across the continent,
# confirming fly-to isn't a no-op — several same-region anomalies
# (Baltic/Poland) were independently confirmed clustered there via a
# direct Postgres query, not a bug.

# Ghost trail confirmed: selecting a live aircraft (icao24 2a077e,
# callsign VEV1993) rendered a dashed amber predicted-position segment
# extending from its marker, distinct from the solid teal corridor
# polyline running alongside it — see
# docs/screenshots/map_closeup_corridors_ghost_trail.png.

# Console: read_console_messages over the full session showed zero
# error/warning entries during live WebSocket updates.
```

Screenshots saved: `docs/screenshots/dashboard_full.png` (full dashboard:
KPIs, pipeline health strip, layer controls, anomaly feed, charts, live
map) and `docs/screenshots/map_closeup_corridors_ghost_trail.png`
(close-up showing a corridor polyline and a selected aircraft's dashed
ghost trail together).

Producer + bronze_stream + silver_stream + the API server + `pnpm dev`
were all left running at the end of this phase.

## Interstitial: full system health check (P1-P7) + India region (2026-07-28/29)

Before starting P8, ran a full health check across every phase with real
commands (not from memory/PROGRESS.md), then added India as a first-class
region alongside Europe/US.

**Health check findings and fixes:**
- All 8 Docker containers healthy, ~2GB/3.83GB memory used. Producer,
  bronze_stream, silver_stream, API all confirmed running (bronze/silver
  had gone stale after a long idle period — restarted from checkpoint,
  zero data loss).
- Silver confirmed actively growing (97,539 -> 98,439 rows in 107s), zero
  `(icao24, time_position)` duplicates, 237 Delta history versions.
- dbt: 11 models, 47/47 tests passing. MLflow: all 4 experiments have
  runs, all 3 registered models (`traffic-forecaster`,
  `trajectory-predictor-lat/lon`) in Production stage. Anomaly rate at the
  time: 3.07% (in the 2-5% target band).
- **Real bug found and fixed**: a fresh API process restart returned
  `NoSuchBucket` from boto3 and silently fell back to local pickles
  instead of the MLflow registry — `ml/config.py` never set
  `MLFLOW_S3_ENDPOINT_URL`/AWS creds anywhere in code, so it only worked
  if a shell happened to have them exported manually from a previous
  session. Fixed by setting them as `os.environ.setdefault(...)` in
  `ml/config.py` at import time, so every process gets MinIO credentials
  for free regardless of how it's launched. Verified: fresh `uvicorn`
  restart now loads both models via `mlflow_registry`.
- **Known, unfixed divergence**: `streaming/jobs/gold_batch.py`'s Delta
  path for `gold/anomaly_events` is a stale placeholder (170 rows) versus
  the real ML pipeline's Postgres table (1,104 rows at the time) — not
  touched, since retiring `gold_batch.py`'s placeholder anomaly builder is
  a design decision beyond a health check's scope.
- Repo hygiene: `.env`/`.env.example` key parity confirmed, no hardcoded
  secrets/absolute paths (only hit: a gitignored dbt build artifact),
  `web/.env.local` was missing from `docs/gitignore-recommended.txt` —
  added.

**India region added** (`REGION` env var: `europe` | `us` | `india` |
`all`, default `india` — the target audience is India-based):
- `ingestion/airports.py`: added `AIRPORTS_INDIA` (DEL, BOM, BLR, MAA,
  HYD, CCU, AMD, COK, PNQ, GOI, JAI, LKO, TRV, GAU, NAG, BBI, IXC, VNS,
  PAT, IDR — real IATA coordinates) and `AIRPORTS_US` (20 major US
  airports, added so "keep US working" has an actual selectable airport
  pool rather than just an unused fixture), selected via `get_airports(region)`.
- `ingestion/config.py`: `REGION_BBOXES` maps each region to an OpenSky
  bounding box; `resolved_bbox()` lets an explicit 4-field override still
  win. `FlightSimulator` and `OpenSkyClient` both now region-aware.
- `streaming/utils/airlines.py`: added Indian ICAO callsign prefixes
  (AIC Air India, IGO IndiGo, SEJ SpiceJet, AXB Air India Express, AKJ
  Akasa Air, VTI Vistara, GOW Go First, LLR Alliance Air).
- `streaming/utils/enrich.py`: added a `South Asia` region box (checked
  before the broader `Asia` catch-all) — India-bbox fixture buckets
  100% "South Asia". `transform/macros/country_to_region.sql` extended to
  match (India/Pakistan/Bangladesh/Sri Lanka/Nepal/Bhutan/Maldives ->
  South Asia); the `accepted_values` test on `int_dim_country.region` was
  extended to allow the new value.
- `tests/test_region_bucketing.py` extended (not replaced) with a third
  fixture test (`opensky_real_sample_india.json`); all 3 region tests plus
  the existing unknown/other-position tests pass (5/5).
- `web/lib/regions.ts` + `web/components/map/RegionController.tsx`: the
  frontend's Region panel (in `LayerControls`) switches between all four
  regions at runtime, recentering the map via `map.setView` (react-leaflet
  doesn't re-apply `center`/`zoom` props after mount). Default driven by
  `NEXT_PUBLIC_DEFAULT_REGION` (default `india`). `pnpm build` clean.

**Real OpenSky India coverage measured before writing any code** (per the
brief's requirement to measure first): two live `/states/all` samples over
`lamin=6.0 lomin=68.0 lamax=37.0 lomax=97.5`, ~90s apart, returned 195 and
203 aircraft — versus 1,234 (Europe fixture) and 879 (US fixture) already
in this repo. About half were India-registered, the rest overflights (UAE,
Singapore, Turkey, etc.). Saved as
`tests/fixtures/opensky_real_sample_india.json`. **Recommendation**: keep
`INGEST_MODE=simulate` as the default for India — OpenSky's volunteer
ADS-B receiver coverage is much sparser over South Asia than Europe/US, so
switching India to live OpenSky mode would look visibly emptier than the
other two regions in a demo. This is documented in `README.md`.

**Corridor + anomaly retrain on India data** (after ~16,352 India rows had
accumulated in silver, restarting the producer with `REGION=india`):
- `python -m ml.corridors`: 509 corridors (up from 238), `eps=0.0185`
  (k-distance knee, k=8), silhouette=0.113, noise=1.8%, over 95,992
  cruise-phase rows (mixed Europe historical + India live data — the full
  silver table, not filtered to one region). **137 of the 509 corridors
  are in Indian airspace** (centroid inside the India bbox), with sensible
  headings and 100-209 members each.
- **Real recalibration finding**: mixing India's own well-formed corridors
  into the same DBSCAN run shifted the whole anomaly score distribution —
  the prior Europe/US-only threshold (0.65, calibrated to land ~3%) dropped
  to a 1.61% flagged rate once India corridors gave India traffic
  somewhere to belong (previously it would have scored "far from every
  corridor" against only Europe/US corridors). Re-picked the threshold to
  0.62 (p97≈0.624) in `ml/anomaly.py`, re-ran, confirmed 3.31% flagged —
  back in the 2-5% band. Documented in-code as a real characteristic of a
  growing multi-region corridor set, not a one-off bug: re-check this
  threshold whenever the region mix changes materially.
- Final state: `gold.flight_corridors` = 509 rows, `gold.anomaly_events` =
  4,341 rows (412 in Indian airspace), `gold.corridor_assignments` =
  95,992 rows. `dbt run` + `dbt test` re-verified 47/47 passing afterward
  (note: `ml/anomaly.py`'s `DROP TABLE ... CASCADE` on
  `gold.anomaly_events` drops the dependent dbt staging view each time —
  `dbt run` must be re-run after any anomaly retrain before `dbt test`).

At the user's request, the producer/bronze_stream/silver_stream were kept
running India-only (not switched to `REGION=all`) so India data keeps
accumulating; the frontend (`pnpm dev`) and API (`uvicorn`) were stopped
at the user's request while this work was in progress.

## Two follow-up fixes before P8 (2026-07-29)

**FIX 1 — corridor quality collapsed (silhouette 0.607 -> 0.113).**
Confirmed the cause: `ml/corridors.py` fit a single `StandardScaler` +
DBSCAN across Europe and India combined — two regions thousands of km
apart — which distorts the scaled feature space and makes a single `eps`
meaningless for either region's actual local density. Rewrote
`ml/corridors.py` to fit per region: separate scaler, separate k-distance
elbow, separate `eps`, `region` column on every corridor row, corridor IDs
kept globally unique via a per-region offset.

A second, independent bug surfaced while testing the fix: the initial
per-region bounding boxes were copied from `ingestion/config.py`'s tight
*live-OpenSky-query* bboxes (Europe lat 45-56°), which cut off Madrid
(40.5°), Barcelona (41.3°), Rome (41.8°), and Lisbon (38.8°) — all real
Europe simulator airports — fragmenting real corridors at the boundary
into a meaningless "other" bucket. Fixed by widening the boxes to match
`streaming/utils/enrich.py`'s existing continental `region_bucket()`
boxes (Europe 34-72°/-25-45°, India matching its "South Asia" box), which
comfortably contain every airport in `ingestion/airports.py`.

A third hypothesis was tested and **rejected**: scaling `min_samples`
with each region's row count (since a fixed k's k-th-nearest-neighbor
distance mechanically shrinks as more data accumulates, independent of
any real change in corridor structure — measured: Europe's auto-selected
`eps` dropped from 0.055 to 0.033 as its row count grew from 44K to 88K
over the session, fragmenting 228 corridors into 363). Tried scaling
`min_samples` proportionally (matching the ratio from the original
0.607-silhouette run, 8/15,000): this made silhouette **worse**, not
better (Europe -0.081 -> -0.157, India 0.105 -> 0.081). Reverted rather
than keep tuning until a number looked right — documented directly in
`ml/corridors.py` so a future reader doesn't re-attempt the same idea.

**Final honest result**: Europe silhouette -0.081, India 0.060 (further
drifted from 0.105 purely because India's row count kept growing between
runs via the live producer — same k-distance-density sensitivity, not a
new issue). **Neither region reached the 0.4 target.** Root cause,
per direct sanity-check evidence rather than speculation: the corridors
themselves check out as real routes — corridor 377's polyline starts at
`[13.10, 77.70]`, within 0.1° of BLR's actual coordinates
(13.20, 77.71), heading due north; corridor 373 runs Maharashtra ->
Haryana (a plausible BOM->DEL-shaped route); corridor 431 runs the Andhra
coast -> near Kolkata (a plausible HYD/MAA->CCU-shaped route). DBSCAN's
density-based partition appears to be finding real, airport-anchored
structure. The low/negative silhouette looks like a genuine mismatch
between the metric (built for globular, well-separated clusters) and the
actual geometry of a hub-and-spoke network with only 20 airports, where
many corridors converge near shared airports by construction — not
something further eps/min_samples tuning is expected to fix.

Recalibrated the anomaly threshold on the new per-region corridors:
`gold.flight_corridors` now 586 rows (363 Europe, 223 India),
`gold.anomaly_events` 5,124 rows, flagged rate 3.13% at the existing
threshold of 0.62 (no re-pick needed this time — happened to still land
in the 2-5% band).

**FIX 2 — `DROP TABLE ... CASCADE` broke dbt on every retrain.**
`ml/anomaly.py` used to `DROP TABLE IF EXISTS gold.anomaly_events CASCADE`
before rewriting the table every retrain, which also dropped dbt's
dependent `staging.stg_anomaly_events` view — silently requiring `dbt run`
before `dbt test` after every retrain, an ordering constraint that would
have broken once P8's `daily_ml_retrain` and `daily_dbt` DAGs run on
independent schedules. Fixed at the root: `TRUNCATE TABLE` (when the
table already exists) + `to_sql(if_exists="append")`, which never touches
the table's identity, so the dbt view survives untouched. **Proven**: ran
`python -m ml.anomaly`, then `dbt test` directly — with no `dbt run` in
between — and got 47/47 passing.

**Also fixed**: `streaming/jobs/gold_batch.py` used to also build and
write its own naive rule-based `anomaly_events` table (a P3-era
placeholder, superseded by `ml/anomaly.py` since P5) to both Delta and
Postgres. Since P8 plans an `hourly_compaction` DAG for this job running
alongside a separate `daily_ml_retrain` DAG, the two would have
periodically clobbered each other's `gold.anomaly_events` — removed the
placeholder builder from `gold_batch.py` entirely; `ml/anomaly.py` is now
the table's sole owner. Deleted the stale Delta path
(`s3a://liveflights/gold/anomaly_events`, 10 leftover objects, ~170-row
placeholder) that this old builder had left behind.

API and frontend restarted and confirmed healthy (`/health` fully green,
`mlflow_registry` for both models, frontend HTTP 200) — the system is
demoable. `ruff check .`, `pytest` (55 passed), `dbt test` (47/47) all
re-verified clean after these changes.

## India-only MVP cleanup + a third DROP-vs-TRUNCATE instance (2026-07-29)

**Simulator callsigns were fully random.** `ingestion/simulator.py`'s
`_random_callsign()` generated 3 random uppercase letters + digits, so no
simulated aircraft ever matched `streaming/utils/airlines.py`'s
`AIRLINE_PREFIXES` lookup — `gold.airline_activity` showed "Unknown/Other"
as its largest bucket regardless of region. Fixed by adding
`_CALLSIGN_PREFIXES_BY_REGION`, a weighted-choice prefix table per region
(India: IndiGo 50 / Air India 20 / Vistara 10 / SpiceJet 8 / Akasa 6 / Air
India Express 4 / Alliance Air 2, approximating real market share; Europe
and US pools added too), threaded through `FlightSimulator` via a new
`self.region` attribute. `GOW` (Go First) deliberately excluded from the
weight table despite still being a valid `AIRLINE_PREFIXES` entry — the
airline ceased operations in 2023.

**A third instance of the DROP-vs-TRUNCATE bug**, this time in two places:
`streaming/jobs/gold_batch.py`'s JDBC write used
`.mode("overwrite")` without `.option("truncate","true")`, so Spark issued
a plain `DROP TABLE` before rewriting — this crashed outright the first
time it ran after dbt built `staging.stg_traffic_by_hour` on top of
`gold.traffic_by_hour` (`cannot drop table ... because other objects
depend on it`). Fixed with `.option("truncate","true")` (Spark JDBC then
issues `TRUNCATE` instead of `DROP`+`CREATE`). Separately, `ml/corridors.py`
(`gold.flight_corridors`, `gold.corridor_assignments`) and
`ml/trajectory.py` (`gold.trajectory_predictions`) both used pandas'
`to_sql(..., if_exists="replace")`, which also drops+recreates — no dbt
view depends on either table yet, so this was dormant rather than
crashing, but it is the identical bug class and would have broken the
moment a staging view was added. Fixed both to `to_regclass(...) IS NOT
NULL` existence check + `TRUNCATE` + `to_sql(if_exists="append")`, matching
the pattern already used in `ml/anomaly.py` and `gold_batch.py`.

**Pivoted project scope to an India-only MVP** at the user's explicit
direction: "at this moment... INDIA ka data sahi se dikhe aur compute ho."
Cleaned `silver` (Delta `UPDATE`/`DELETE`, not a rebuild) in three steps,
after first stopping `producer`/`bronze_stream`/`silver_stream` (a live
`silver_stream` writer caused the first cleanup attempt to fail with
`ConcurrentAppendException` — Delta's optimistic concurrency control
correctly rejected a concurrent `UPDATE` against an in-flight `MERGE`):
1. Relabelled `region='Asia'` rows (India rows mislabelled before the
   "South Asia" bucket existed) to `'South Asia'` — 16,288 rows, preserving
   the underlying real accumulated data rather than deleting it.
2. Deleted `source='simulate' AND region != 'South Asia'` — Europe/Unknown
   simulator junk, 113,253 rows.
3. Deleted the remaining India rows with unmatched (pre-fix random)
   callsign prefixes — 52,151 rows.
`source='opensky'` rows (1,234) were explicitly never touched by any of
the three steps, at the user's direction — the only authentic
non-simulated data in the project, kept specifically to prove the platform
is region-agnostic rather than India-only by construction.

Silver: **195,189 -> 29,785 rows** (28,551 clean India / 1,234 real
OpenSky). Re-ran the full downstream chain on the cleaned data:
- **Corridors**: 23,972 cruise rows -> 181 total (180 India, 1 Europe).
  India silhouette **0.2016** (up from -0.017 pre-cleanup, still short of
  the 0.4 target but a real improvement — cleaner input data measurably
  helped). Europe now only 627 cruise rows -> 1 cluster, silhouette
  undefined (needs >=2 clusters) — expected once real OpenSky is the only
  Europe source; kept only as the region-agnostic proof point, not
  production-grade.
- **gold_batch**: all 4 tables rewritten cleanly (traffic_by_hour 6,
  traffic_by_country 51, airline_activity 30, altitude_band_distribution 8
  rows) — no crash, confirming the TRUNCATE fix.
- **Anomaly rescoring**: flagged rate 3.64% at the existing threshold
  (0.62) — still comfortably in the 2-5% band, no re-pick needed.
- **`airline_activity`**: India carriers now populated correctly (IndiGo
  135, Air India 51, Vistara 30, SpiceJet 18, Akasa 16, Air India Express
  11, Alliance Air 5, all distinct-aircraft counts). "Unknown/Other" (816)
  is now attributable entirely to the 1,234 real OpenSky rows (verified:
  Europe-matched + Unknown/Other sums to ~1,234) — i.e. genuinely
  unmapped real-world callsigns, not simulator junk. This is the correct,
  honest end state per the original cleanup instruction: "real OpenSky
  rows... are legitimately Unknown/Other if unmatched."
- **dbt**: `dbt run` (11/11) then `dbt test` (47/47) both clean.

Stopped `producer`/`bronze_stream`/`silver_stream` (already down, moved
earlier than planned once the `ConcurrentAppendException` made clear the
cleanup couldn't run against a live writer). API and frontend left
running throughout — `/health` still green (`kafka_live_store` correctly
reports "no messages received recently" now that ingestion is stopped;
not a bug).

## Next up

- **P8**: see `PLAN.md` for the next phase.

---

## READY TO COMMIT (P1 + P2)

Suggested this covers two logical commits, but since both are done in one
session here's a combined summary — split however you'd like when you commit:

**Files added:**
```
PLAN.md, CLAUDE.md, PROGRESS.md
.env.example, .env (local only — do not commit; see docs/gitignore-recommended.txt)
docker-compose.yml, Makefile, pyproject.toml
docs/gitignore-recommended.txt
infra/prometheus/prometheus.yml
infra/grafana/provisioning/datasources/datasources.yml
infra/grafana/provisioning/dashboards/dashboards.yml
infra/postgres/init/001_schemas.sql
ingestion/__init__.py
ingestion/config.py
ingestion/airports.py
ingestion/simulator.py
ingestion/opensky.py
ingestion/producer.py
ingestion/schemas/__init__.py
ingestion/schemas/flight_state.py
ingestion/tests/__init__.py
ingestion/tests/test_simulator.py
ingestion/tests/test_producer.py
ingestion/schemas/opensky_raw.py
ingestion/replay.py
tests/__init__.py
tests/test_schema_contract.py
tests/fixtures/opensky_real_sample.json
tests/fixtures/opensky_real_sample_us.json
uv.lock
```

**Modified for schema freeze:**
```
ingestion/opensky.py       (parse rows via OpenSkyStateVector, not manual dict/zip)
ingestion/producer.py      (add --mode replay + --fixture-path)
ingestion/schemas/__init__.py
ingestion/schemas/flight_state.py (fill in remaining field descriptions)
```

**Suggested additional commit message:**
```
test(ingestion): freeze OpenSky schema against two live captures

Derive OpenSkyStateVector directly from two independent real API captures
(Europe + US bbox) instead of assumed field types, add a schema-contract
test that fails loudly if the simulator or FlightState model drifts from
the live shape, and add a replay mode that streams a saved capture through
the same producer pipeline as simulate/opensky modes.
```

**Suggested commit messages:**
```
feat(infra): scaffold repo structure and local docker-compose stack

Bring up redpanda, minio, postgres, redis, mlflow, prometheus, and grafana
via a single `make up`, with health checks and bootstrap jobs (bucket
creation, schema creation) so the stack is demoable from a clean checkout.
```
```
feat(ingestion): add flight simulator, OpenSky client, and Kafka producer

Add a great-circle flight simulator (no external deps) as the default demo
path, an OpenSky OAuth2 client with anonymous fallback, and a producer that
validates records against a shared FlightState schema and routes failures
to a DLQ topic.
```

---

## READY TO COMMIT (P3)

**Files added:**
```
streaming/__init__.py
streaming/config.py
streaming/session.py
streaming/schemas.py
streaming/utils/__init__.py
streaming/utils/enrich.py
streaming/utils/airlines.py
streaming/jobs/__init__.py
streaming/jobs/bronze_stream.py
streaming/jobs/silver_stream.py
streaming/jobs/gold_batch.py
tests/test_region_bucketing.py
```

**Files modified:**
```
pyproject.toml               (add `streaming` dependency group: pyspark,
                               delta-spark, psycopg2-binary, pygeohash)
docker-compose.yml            (postgres host port 5432 -> 5433, see below)
.env / .env.example           (POSTGRES_PORT + DATABASE_URL updated to 5433,
                               with an inline comment explaining why)
```

**Not committed (data/runtime artifacts, already in
docs/gitignore-recommended.txt):** `data/checkpoints/`, anything under the
MinIO `liveflights` bucket (bronze/silver/gold Delta+Parquet output),
`~/.ivy2` jar cache.

**Suggested commit messages:**
```
feat(streaming): add Spark bronze/silver/gold pipeline on Delta/MinIO

Pin PySpark 3.5.3 + Delta 3.2.1 + hadoop-aws 3.3.4 as a verified-compatible
set, add a single get_spark_session() helper shared by all three jobs, and
implement bronze (Kafka->Parquet), silver (bronze->Delta with MERGE-based
idempotent dedup + enrichment), and gold (batch aggregates + JDBC upsert
into Postgres). Region bucketing validated against both real fixtures from
the P2 schema freeze.
```
```
fix(infra): remap Postgres to host port 5433

A pre-existing Homebrew postgresql@17 service was already bound to
127.0.0.1:5432/[::1]:5432, silently intercepting connections meant for the
Dockerized Postgres and causing JDBC writes to fail with a misleading
"role does not exist" error. Remap our container off 5432 entirely rather
than depend on an unrelated host service being absent.
```
```

---

## READY TO COMMIT (P3 fixes + P4)

**Files added:**
```
transform/dbt_project.yml
transform/profiles.yml
transform/macros/generate_schema_name.sql
transform/macros/generate_surrogate_key.sql
transform/macros/country_to_region.sql
transform/macros/pct_change.sql
transform/models/staging/_sources.yml
transform/models/staging/_staging__models.yml
transform/models/staging/stg_traffic_by_hour.sql
transform/models/staging/stg_traffic_by_country.sql
transform/models/staging/stg_airline_activity.sql
transform/models/staging/stg_altitude_band_distribution.sql
transform/models/staging/stg_anomaly_events.sql
transform/models/intermediate/_intermediate__models.yml
transform/models/intermediate/int_dim_airline.sql
transform/models/intermediate/int_dim_country.sql
transform/models/marts/_marts__models.yml
transform/models/marts/mart_traffic_daily.sql
transform/models/marts/mart_region_summary.sql
transform/models/marts/mart_airline_leaderboard.sql
transform/models/marts/mart_anomaly_summary.sql
transform/snapshots/airline_activity_snapshot.sql
transform/tests/assert_no_negative_flight_counts.sql
```

**Files modified:**
```
pyproject.toml           (add `dbt` dependency group: dbt-core, dbt-postgres;
                           also add streaming's spark_cores config field)
streaming/config.py       (spark_cores setting, default 2; postgres_port
                           default corrected to 5433)
streaming/session.py      (local[*] -> local[{spark_cores}])
.env / .env.example       (SPARK_CORES, SHUFFLE_PARTITIONS, DRIVER_MEMORY,
                           EXECUTOR_MEMORY, CHECKPOINT_ROOT backfilled)
Makefile                  (test target now includes --group streaming
                           --group dbt; new dbt-debug/run/test/snapshot/docs
                           targets)
```

**Not committed:** `transform/target/`, `transform/logs/` (dbt run
artifacts — already in docs/gitignore-recommended.txt).

**Suggested commit messages:**
```
fix(streaming): bound Spark to local[N] instead of local[*]

Multiple concurrent local Spark drivers (bronze, silver, gold, later
Airflow tasks) each grabbing every core caused a multi-minute scheduling
stall during P3 verification. SPARK_CORES is now a configurable env var
(default 2) instead of local[*], and the setting joins the other
previously-code-only streaming tunables in .env.example.
```
```
feat(transform): add dbt project over the gold Postgres tables

Staging views for all 5 gold tables, intermediate airline/country
dimensions (with a surrogate-key macro and a country->region business
mapping), and 4 marts (traffic_daily, region_summary, airline_leaderboard,
anomaly_summary). 47 tests (not_null/unique/accepted_values/relationships
plus one singular test), one snapshot, one custom schema-naming macro so
models land in the already-provisioned staging/analytics schemas, and
generated docs.
```

---

## READY TO COMMIT (P5 checks + P6)

**Files added:**
```
api/config.py
api/main.py
api/middleware.py
api/logging_config.py
api/metrics.py
api/deps/__init__.py
api/deps/db.py
api/deps/cache.py
api/models/__init__.py
api/models/common.py
api/models/flights.py
api/models/stats.py
api/models/anomalies.py
api/models/corridors.py
api/models/forecast.py
api/services/__init__.py
api/services/live_store.py
api/services/models_loader.py
api/services/stats_service.py
api/services/anomaly_service.py
api/services/corridor_service.py
api/services/trajectory_service.py
api/services/forecast_service.py
api/routers/__init__.py
api/routers/health.py
api/routers/flights.py
api/routers/stats.py
api/routers/anomalies.py
api/routers/corridors.py
api/routers/forecast.py
api/routers/ws.py
```

**Files modified:**
```
pyproject.toml               (add `api` dependency group)
streaming/utils/enrich.py    (lazy-import pygeohash so the API doesn't
                               need it just to reuse data_quality_flags)
ml/trajectory.py             (P5 CHECK 2: actual-elapsed-time dead
                               reckoning, dt distribution report, source
                               breakdown, eval_phase column fix)
ml/anomaly.py                (P5 CHECK 3: recalibrated threshold 0.5->0.65,
                               score distribution report, top-N ML-only
                               detection dump, CASCADE fix for the
                               gold.anomaly_events rewrite)
README.md                    (Model 2 simulator-only-data limitation)
```

**Suggested commit messages:**
```
fix(ml): diagnose and fix the trajectory baseline, recalibrate anomaly threshold

Use each pair's actual elapsed time (not a nominal 300s) in the dead-
reckoning baseline, add a per-source (simulate/opensky) error breakdown
that surfaces a real limitation (100% of pairs are simulator-sourced),
and recalibrate the contextual anomaly threshold from 0.5 (27% flagged)
to 0.65 (3% flagged, in the requested 2-5% band).
```
```
feat(api): add FastAPI backend — REST + WebSocket, Redis cache, Prometheus

Live positions served from an in-memory store fed by a dedicated Kafka
consumer thread (not Postgres/Delta, too slow for the hot path). All
endpoints from the brief implemented with OpenAPI docs, Redis caching on
stats (X-Cache header, verified hit/miss timing), request-ID + JSON
logging, Prometheus /metrics, and models 2/4 loaded via ml/registry.py
with a verified MLflow-registry/local-pickle fallback.
```

---

## READY TO COMMIT (P7 + India region + post-P7 fixes, consolidated)

Everything below covers all work since the P6 commit point above: the P7
frontend, the full P1-P7 health check, adding India as a region, the two
follow-up fixes before P8, and this session's India-only MVP cleanup +
third DROP-vs-TRUNCATE fix. Grouped into logical commits.

**Files added:**
```
web/                                  (entire Next.js 14 app — P7)
docs/screenshots/                     (P7 verification screenshots)
tests/fixtures/opensky_real_sample_india.json
```

**Files modified:**
```
ml/config.py                 (set MLFLOW_S3_ENDPOINT_URL/AWS_* env vars at
                               import time — health-check bug fix, API was
                               silently falling back to local pickles)
docs/gitignore-recommended.txt (web/.env.local)

ingestion/airports.py        (AIRPORTS -> AIRPORTS_EUROPE + AIRPORTS_US +
                               AIRPORTS_INDIA, get_airports(region))
ingestion/config.py           (REGION_BBOXES, region field, resolved_bbox())
ingestion/opensky.py          (use resolved_bbox())
ingestion/simulator.py        (region-selected airports; weighted real
                               airline-callsign prefixes per region,
                               replacing fully-random callsigns)
ingestion/producer.py         (pass region= to FlightSimulator)
.env / .env.example            (REGION=india, commented-out BBOX_* fields)

streaming/utils/airlines.py   (Indian ICAO airline prefixes)
streaming/utils/enrich.py     (South Asia region box)
streaming/jobs/gold_batch.py  (removed placeholder anomaly_events builder;
                               .option("truncate","true") on the JDBC
                               write — 3rd DROP-vs-TRUNCATE fix)

transform/macros/country_to_region.sql          (South Asia bucket)
transform/models/intermediate/_intermediate__models.yml (accepted_values)

ml/corridors.py               (per-region DBSCAN fitting; wide continental
                               bboxes; TRUNCATE instead of
                               to_sql(if_exists="replace") — 3rd
                               DROP-vs-TRUNCATE fix, 2nd location)
ml/anomaly.py                 (TRUNCATE instead of DROP CASCADE; threshold
                               0.65 -> 0.62 after India corridors added)
ml/trajectory.py              (TRUNCATE instead of to_sql(if_exists=
                               "replace") — 3rd DROP-vs-TRUNCATE fix, 3rd
                               location; pre-emptive, no dbt view depends
                               on this table yet)

tests/test_region_bucketing.py (India fixture test)
README.md                      (Region configuration section)
PROGRESS.md                    (this file)
```

**Data changes (not code, but state-changing and worth recording):**
```
silver Delta table cleaned: 195,189 -> 29,785 rows (India-only MVP pivot;
  see "India-only MVP cleanup" section above for the exact three-step
  UPDATE/DELETE and the ConcurrentAppendException that required stopping
  producer/bronze_stream/silver_stream first)
gold.flight_corridors, gold.corridor_assignments, gold.anomaly_events,
  gold.traffic_by_hour, gold.traffic_by_country, gold.airline_activity,
  gold.altitude_band_distribution all rewritten against the cleaned data
stale gold/anomaly_events Delta path in MinIO deleted (10 leftover
  objects from the old gold_batch.py placeholder builder)
```

**Suggested commit messages:**
```
feat(web): add Next.js 14 flight dashboard (P7)

ATC-style dark map UI on react-leaflet with an imperative marker registry
for 150+ aircraft on 3s ticks, CARTO dark_matter tiles, WebSocket
reconnect with backoff, corridor overlays, ghost trails, and an anomaly
feed with test records filtered by default.
```
```
fix(ml): correct a live MLflow/MinIO env-var gap found in the P1-P7 health check

ml/config.py never set MLFLOW_S3_ENDPOINT_URL or AWS credentials, so the
API silently fell back to local pickles instead of the MLflow registry.
Set them at import time from the existing MinIO settings.
```
```
feat: add India as a first-class region

20 real Indian airports, Indian ICAO airline prefixes, a "South Asia"
region bucket end-to-end (streaming enrichment, dbt macro, tests), and a
frontend region selector defaulting to India. Real OpenSky coverage over
India measured before any code changes (~195-205 aircraft vs 1,234
Europe/879 US fixtures) — sparse, so simulate mode stays the default.
```
```
fix(ml): fit corridor discovery per region instead of combined

A single StandardScaler+DBSCAN fit across Europe and India collapsed
silhouette from 0.607 to 0.113 by distorting the shared scaled feature
space. Fit each region separately (own scaler, own k-distance-elbow eps),
widened the region bboxes to match streaming's continental buckets
(the tight ingestion bboxes were fragmenting real corridors at the
boundary), and recalibrated the anomaly threshold afterward. Tested and
rejected scaling min_samples with region size — measurably made
silhouette worse.
```
```
fix(ml,streaming): stop dropping tables that dbt views depend on

Three independent instances of the same bug: ml/anomaly.py, gold_batch.py,
and now ml/corridors.py/ml/trajectory.py all rewrote gold tables via
DROP TABLE CASCADE or an equivalent (JDBC overwrite without truncate=true,
pandas to_sql if_exists="replace") — each one silently or loudly breaks
any dbt staging view built on top. Standardized on TRUNCATE + append
everywhere so a table's identity, and any dbt view on it, survive a
retrain untouched. Proven for the anomaly path: dbt test passes 47/47
immediately after a retrain with no dbt run in between.
```
```
fix(ingestion): simulate real airline callsigns instead of random ones

FlightSimulator generated fully random 3-letter callsign prefixes, so no
simulated aircraft ever matched the airline lookup table and
gold.airline_activity showed "Unknown/Other" as its largest bucket
regardless of region. Added a weighted real-airline-prefix table per
region (India: IndiGo/Air India/Vistara/SpiceJet/Akasa/Air India
Express/Alliance Air, approximating real market share; Europe and US
pools too), deliberately excluding Go First since it ceased operations
in 2023.
```
```
chore: pivot to an India-only MVP, clean stale simulator rows from silver

Relabelled India rows mislabelled region='Asia' (pre-existing "South
Asia" bucket fix) to their correct region, deleted all non-India
simulator rows and India rows with pre-fix random callsigns, and kept the
1,234 real OpenSky rows untouched as the project's only authentic data
and its region-agnostic proof point. Re-ran corridors, gold_batch,
anomaly rescoring, and dbt on the cleaned data — India corridor
silhouette improved from -0.017 to 0.20, airline_activity now shows real
Indian carriers, dbt test still 47/47.
```

## P10 — Docs, then a live demo switch to real OpenSky data (2026-07-29)

**P10 (README)**: `README.md` fully rewritten as a portfolio piece per the
13-section brief — badges, screenshot-first, a mermaid architecture diagram
(rendered and verified with `@mermaid-js/mermaid-cli`, no syntax errors),
quickstart, data model, a real-numbers ML section, testing, a metrics
table, an honest limitations section, engineering notes framed as
debugging depth, tech stack, project tree, roadmap. Every number pulled
from this file; one internal inconsistency was caught and fixed before
publishing (the trajectory table had mixed medians from one run with a p90
column from a different, differently-sized run — fixed to use one
internally consistent run, with the larger re-verification run described
in prose instead).

**Live demo walkthrough, then a real bug found**: user asked to see the
dashboard live, then asked directly whether it was running on real data or
simulation — honest answer: `INGEST_MODE=simulate`. User then asked to
switch to real data. Restarted the producer with `--mode opensky`
(anonymous polling, no credentials configured, 60s interval) — confirmed
live via the actual OpenSky API returning real aircraft (e.g. `icao24
fa8efb`, callsign `AIC6555`, a real Air India flight).

**Found a real bug while doing this**: `api/services/live_store.py`'s
`STALE_AFTER_SECONDS = 120` constant is *only* read by `is_receiving()` (a
feed-health boolean) — `get_all()` never filters `_latest` by recency, so
the in-memory live-flight dict never evicts an aircraft once seen. Old
simulator-mode aircraft (icao24 hex format is indistinguishable from real
ICAO24 addresses) stayed in the live feed indefinitely after switching to
`--mode opensky`, inflating the dashboard's "active flights"/"countries"
KPIs with stale simulated entries mixed into real ones. Confirmed directly
via `/api/flights/live`'s `source` field: 775 simulate + 260 opensky in the
same response, long after the simulate producer had stopped. **Worked
around, not fixed at the root**: restarted the API process, which clears
the in-memory store (a fresh Kafka consumer group only sees new messages
from `latest`). Confirmed clean afterward: 251/251 entries `source:
opensky`. **Not fixed in code** — a proper fix would filter `get_all()`
(and `count()`/`countries()`/`avg_altitude_ft()`) by
`STALE_AFTER_SECONDS`, or evict on a timer; noted here for a future
session rather than scope-creeping into an unplanned live-store rewrite
mid-demo.

**Corridors + anomaly retrained on the real-OpenSky-fed data**:
`ml.corridors`: 271 total corridors (270 India, 1 Europe), India
`eps=0.0534`, silhouette **0.1956** (stable vs. the 0.2016 pre-real-data
baseline — same ballpark, consistent with the known eps-drifts-with-data-
volume pattern, not a regression). `ml.anomaly` at the existing 0.62
threshold flagged **6.34%** — outside the 2-5% band, because real traffic
produces more corridor-unassigned/noise points than the simulator's tight
synthetic routes did, shifting the score distribution (percentiles:
p95=0.634, p96=0.658, **p97=0.784** — a sharp jump between p96/p97).
Re-calibrated `ANOMALY_SCORE_THRESHOLD` from 0.62 to **0.78** (≈p97),
re-ran, confirmed **3.14%** flagged — back in-band. `dbt test` re-verified
47/47 passing with no `dbt run` first, confirming the TRUNCATE fix still
holds after this retrain cycle.

**Full health check after all of the above**: 8/8 Docker containers
healthy, producer(opensky)/bronze_stream/silver_stream all running,
`ruff check .` clean, `pytest` 55 passed, `dbt test` 47/47, API `/health`
fully green (`kafka_live_store: true`), frontend HTTP 200.

**Repo status**: this directory is not yet a git repository — no commits
have been made at any point in this project's history. See the READY TO
COMMIT block below for the exact commands to initialize it and push to
GitHub (per project rules, no git command has been run automatically).
```
