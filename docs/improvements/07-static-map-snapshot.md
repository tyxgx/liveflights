# 07 · Serve the map from a small pre-gzipped S3 file instead of the API Lambda

Status: **frontend deployed and live; infra change written, needs one `terraform apply`** (until then the page uses the
fallback path described below, and a hand-written snapshot was used to test the fast path)

## Situation

Every 15 s each open tab asked API Gateway and the API Lambda for `/api/flights/live?limit=6000`. The Lambda read the
whole 1.6 MB `live/latest.json` from S3, parsed it, re-serialised it and returned it **uncompressed** (API Gateway HTTP APIs do
not compress for you). Measured on production: 1.6 MB per poll, 1 to 5 s per call, and several tabs or panels at once
meant the account's 10 concurrent Lambdas (shared with another project) were regularly used up: throttles were
9, 21 and 172 on 09-28, 09-29 and 10-02 ([01](01-aws-cost-audit.md)). The data only changes once a minute, so
about three of every four polls were identical re-downloads.

## Task

Take the biggest request off the Lambda entirely, make it small, and let the browser cache it, without making the
site depend on a new piece of infrastructure working: if the new file is missing or stale the page must keep
working exactly as before.

## Action

- **Ingest Lambda** (`infra/terraform/lambda_ingest/handler.py`): after writing `live/latest.json` it also writes
  `live/map.json` to the **public site bucket**: same shape as the API response, five fields the web app never reads
  dropped (`time_position`, `last_contact`, `geo_altitude`, `spi`, `position_source`), coordinates rounded to
  5 decimals (about 1 m), speeds and altitudes to 1 decimal, gzip level 6, `Content-Encoding: gzip`,
  `Cache-Control: public, max-age=10`. `live/latest.json`, Firehose and Bronze are untouched (still all 16 fields). A
  failure here is logged and ignored: it can never stop core ingestion.
- **Terraform**: `SITE_BUCKET_NAME` env var; `s3:PutObject` on exactly `site/live/map.json`; a CORS rule (GET/HEAD) on
  the site bucket, because the S3 website endpoint and the REST endpoint are different origins.
- **Frontend** (`NEXT_PUBLIC_MAP_SNAPSHOT_URL` in `.env.production`, `api.liveSnapshot`, `useFlightsPolling`): try the
  snapshot first; use the API if the snapshot is missing, unreachable, empty or older than 3 minutes; if its
  `updated_at` is the same as last time, skip the state update entirely (no re-render of 3,600 markers).
- **Found while measuring: `/api/corridors?limit=5000` is 806 KB and was refetched every 2 minutes**, although the
  corridors are a model artifact that only changes when the model is retrained. It now loads once per page visit.
- `tests/test_ingest_map_snapshot.py` (2 tests): shape, dropped fields, rounding, gzip headers, nulls kept, input not mutated.

## Result

Size, from real data (3,629 aircraft): API response today 1,630,959 B, compact JSON 1,500,815 B, **map.json gzipped 137,488 B
(8% of the old size)**; the object written to S3 measured 137,871 B.

Production, headless Chrome, 30 s, with a real snapshot written by the real `_write_map_snapshot` (the Lambda will
rewrite it every minute after the apply) (`data/step05-prod-*` before, `data/step07-prod-*` after):

| | Before (step 05) CPU 1x | After CPU 1x | Before CPU 4x | After CPU 4x |
|---|---|---|---|---|
| Transferred in 30 s | 4,403 KB | **1,609 KB** | 4,461 KB | **1,609 KB** |
| API calls / API KB | 18 / 3,949 | 17 / 1,020 | 23 / 4,008 | 17 / 1,020 |
| First aircraft on screen | 17.6 s | **8.1 s** | 23.1 s | **4.9 s** |
| Avg API call | 4.9 s | 2.0 s | 5.1 s | 2.2 s |
| FPS / main thread blocked | 42.3 / 6.8 % | 32.3 / 10.2 % | 11.6 / 80.7 % | 12.9 / 72.5 % |

Over 135 s on the live site: `map.json` was requested 9 times but only about 136 KB crossed the network (the rest came from
the browser cache); corridors were fetched once instead of every 2 minutes.

`behavior-check.mjs` against production: all 6 pass; the 3,627 markers on screen equal the snapshot's count, which shows the
fast path was the one in use.

**Not improved / honest notes.**
- FPS at CPU 1x went down from 42 to 32 between two single runs while "blocked" rose from 6.8 % to 10.2 %. Rendering code did
  not change in this step; I treat this as run-to-run noise (earlier same-code runs on the same day ranged 29.6 to 42.3), not a result.
  CPU 4x is the cleaner comparison and did not get worse.
- About 1 MB per 30 s still comes from the API: `/api/anomalies` is requested by two components with different page sizes
  (50 and 100) at 10 s and 15 s, 21 KB to 40 KB each and about 600 KB per 135 s, plus corridors on first load. These are the next
  candidates (one shared poll; API gzip).
- Until the apply the Lambda does not write `map.json`; the snapshot I wrote by hand ages out after 3 minutes and the page
  silently uses the API. That fallback is the designed behaviour and was seen working (behaviour check passed with the file absent).
- The snapshot is world-readable, like the rest of the site bucket. It contains the same public ADS-B data the map already shows.

**Apply** (the agent environment cannot run `terraform apply`; plan is 1 to add, 3 to change, 0 to destroy):
`bash run_logs/run.sh tf_apply_step06_07 "cd infra/terraform && terraform apply -auto-approve"`

**Rollback:** remove `SITE_BUCKET_NAME` (the Lambda stops writing it) or set `NEXT_PUBLIC_MAP_SNAPSHOT_URL=` empty and redeploy
the site: the page goes back to polling the API. `aws s3 rm s3://liveflights-prod-site-922120357133/live/map.json` deletes the file.
