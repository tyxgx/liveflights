# Data quality, testing, and metrics

## Data quality and testing

- **47 dbt tests** across staging/intermediate/marts: `not_null`, `unique`, `accepted_values`, one `relationships` test, and a singular test asserting no negative flight counts.
- **Schema contract test** (`tests/test_schema_contract.py`, 7 test functions covering field-name/type/nullability parity, callsign normalisation, and a round-trip check) proves simulator output and two independent real OpenSky captures (Europe, 1,234 states; US, 879 states) validate through the identical `FlightState` model — sanity-checked as non-vacuous by injecting a fake type drift into a throwaway copy and confirming it fails loudly.
- **Exactly-once semantics**: Spark Structured Streaming checkpointing (`./data/checkpoints`) plus an idempotent Delta `MERGE` keyed on `(icao24, time_position)` in silver — not reliance on streaming `dropDuplicates`, which is only bounded by the watermark window. Proved by replaying the same 1,234-record real fixture twice: silver's opensky-sourced row count stayed at 1,234, not 2,468.
- **Restart-safety proof**: published 5 uniquely-tagged records, `kill -9`'d `silver_stream` before it could process them (confirmed via a direct count showing zero), restarted from checkpoint, and confirmed all three independent signals agreed — exact row-count delta (+5), exact content match, and a new Delta commit timestamped after the restart. Rules out "restarted but silently did nothing" as a false positive.

## Metrics (local stack)

| Metric | Value |
|---|---|
| Bronze rows | 198,857 |
| Silver rows (post India-MVP cleanup) | 29,785 (28,551 India, 1,234 real OpenSky) |
| dbt tests passing | 47/47 |
| pytest suite | 55 passed |
| Corridors discovered | 181 (180 India, 1 Europe) |
| India corridor silhouette | 0.2016 |
| Anomaly flagged rate | 3.64% (target band 2-5%) |
| Trajectory model vs dead reckoning (overall median, n=4,394) | 2.489 km vs 9.004 km |
| Forecast model MAE / RMSE / MAPE | 8.506 / 10.814 / 0.1057 |
| API p95 latency — `/api/anomalies?page_size=50` | 45.0 ms |
| API p95 latency — `/api/corridors?limit=50` | 10.3 ms |
| API p95 latency — `/api/flights/live?limit=500` | 1.9 ms |

**Cloud (AWS) pipeline** — a separate, much smaller live system; not comparable to the local numbers above (different data volume, different network path). Figures below are from the initial India-region deployment; see [aws-architecture.md](aws-architecture.md) for the current, much-higher-volume Europe numbers.

| Metric | Value |
|---|---|
| Step Functions execution runtime (bronze→silver→gold, incl. table validation) | ~15s |
| Rows processed per transform run | 800 bronze → 800 silver rows, 4 gold tables (1–6 rows each) |
| Ingest Lambda invocation result | 40 simulated states/invocation, every 5 min |
| DLQ messages | 0 |
| API Gateway `/health` round trip (Mumbai → us-east-1, cross-region, cold Lambda not excluded) | ~630–660ms median over 10 samples |
| Athena query (`SELECT * FROM gold.traffic_by_country`) | returns rows in ~2–3s |

## Limitations

- **The dataset is simulator-dominant.** Of 29,785 silver rows, 28,551 are simulated and only 1,234 are real OpenSky captures — kept deliberately as the project's only authentic data and its region-agnostic proof point, not because they're statistically sufficient on their own.
- **Real OpenSky coverage over India is sparse**: a live sample returned ~195-205 aircraft, versus 1,234 for the Europe fixture and 879 for the US fixture used elsewhere in this repo. OpenSky's coverage depends on volunteer ADS-B ground receivers, which are far denser in Europe/US than South Asia — this is a real constraint of the free data source, not a bug in this project.
- **The traffic forecast is trained on synthetic history**, not real accumulated traffic — labelled as such everywhere it surfaces. Real history will need days of continuous accumulation before it's viable to retrain against.
- **Europe is now only 627 cruise rows** after the India-MVP pivot deleted stale pre-fix simulator rows — its single resulting corridor and undefined silhouette (DBSCAN needs ≥2 clusters to compute one) exist only to prove the platform still works region-agnostically, not as a serious Europe corridor model.
- **Corridor silhouette sits below the conventional 0.4-ish "good clustering" threshold** even after fixing two real bugs (combined-region fitting, tight bounding boxes) and testing and rejecting a third hypothesis (scaling `min_samples` with row count made results worse, not better). The remaining gap looks like a genuine mismatch between a metric built for globular clusters and a hub-and-spoke network's elongated, converging corridor geometry — not something further tuning is expected to fix. Validated geometrically instead (corridor endpoints against real airport coordinates).
- **The trajectory model has only ever been evaluated against simulator-generated motion** — all 21,969 valid pairs are `simulate`-sourced, since a valid pair needs the same aircraft observed twice 5 minutes apart, and the real OpenSky data used here is a single-snapshot capture.
