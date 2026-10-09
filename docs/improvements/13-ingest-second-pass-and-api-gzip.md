# 13. Ingest second pass for rate-limited points + gzip on the API

## Situation
On 2026-10-09 adsb.lol answered HTTP 429/420 for several of the polled circles. Some ingest runs returned only 274 of ~2,900 aircraft, because a point that fails after its two retries is dropped for that minute. Those aircraft then had a gap in `live/history.json`; the predict Lambda needs 10 readings spaced 60 s apart and skipped them (`skipped(no history)=3,111`), so live predictions fell from ~2,500 to 24 and took ~15 minutes to recover. Separately, `/api/predictions` is ~2.4 MB of JSON per call and the API sends it uncompressed (no `Content-Encoding`), close to Lambda's 6 MB response limit.

## Task
1. A lost point should not mean a lost minute for its aircraft, without increasing the load that triggers the rate limit.
2. Large API answers should be small on the wire, without changing any response body.

## Action
- `infra/terraform/lambda_ingest/handler.py`: the fan-out is split into `_fetch_points()`. After the first pass, points that failed get **one** slower retry pass (2 workers, 1 s stagger, 2 s pause) only if the first pass took under 40 s (Lambda timeout is 90 s, normal runs 20-45 s). Aircraft already seen in the first pass keep their first observation. If everything still fails the old simulator fallback is unchanged. Every constant is an env var.
- `api/cloud/app.py`: `GZipMiddleware(minimum_size=1000)`. Checked with the exact pinned versions (fastapi 0.115.6, mangum 0.19.0) and a fake API Gateway v2 event: the gzip body comes back base64-encoded with `content-encoding: gzip`, decodes to identical JSON (113,897 B -> 17,020 B, 6.7x); small bodies stay plain; clients that do not send `Accept-Encoding: gzip` get plain JSON.
- `tests/test_ingest_second_pass.py` (4 tests): failed point recovered, only failed points retried, no second pass when the first was slow, first-pass observation wins on overlap, all-fail still raises for the simulator fallback.
- Rejected: carrying forward the previous minute's position for failed points (would show stale positions as fresh); raising the retry count (more load on the endpoint that is already limiting us).

- `api/cloud/app.py`: S3 client with `connect_timeout=3, read_timeout=6` and 3 standard retries. In the 2026-10-09 API logs 2 of ~1,300 calls took 22-25 s (normal ~1 s, p99 otherwise under 2 s); boto3's default read timeout is 60 s, so one stalled S3 read held a whole request. A stalled read now fails after 6 s and retries on a new connection. A cold start is unaffected (init ~1.2 s).

## Measurement of the dashboard today (headless probe, same method as step 02)
| | 02 baseline CPU 1x | now CPU 1x | 02 baseline CPU 4x | now CPU 4x |
|---|---|---|---|---|
| First aircraft visible | 16.4 s | **4.3 s** | 22.0 s | **4.2 s** |
| Frame rate | 31.6 fps | **51.7** | 5.5 | **23.3** |
| Main thread blocked | 19% | **3.6%** | 93.8% | **54.8%** |
| Network total | 6.6 MB | **1.7 MB** | 6.6 MB | **1.7 MB** |
| API responses uncompressed | 21/21 | 16/17 | 23/24 | 17/17 (gzip not deployed yet) |
| Failed requests | one 503 | one timeout on `/api/forecast/traffic` (a 25 s stall) | one 503 | none |

Raw files (local only, `docs/improvements/data/` is gitignored): `now-2026-10-09-cpu1.json`, `now-2026-10-09-cpu4.json`. The remaining weak spot is a slow device (4x CPU: 23 fps, 55% blocked), which is what step 11 (canvas renderer) is for; it needs a visible-tab visual check, so it is not done blind. Note for testing: a hidden/background tab never polls (step 04 pause-when-hidden), so an automation tab shows an empty "Waiting for live data..." state; that is expected, not a bug.

## Result
Tests: 85 passed, ruff clean. **Not deployed:** needs `terraform apply` (ingest zip + api image, Docker must be running). After deploy, verify: `curl -H 'Accept-Encoding: gzip' -I .../api/predictions` shows `content-encoding: gzip`, the dashboard still loads, and the ingest log shows `adsb.lol second pass: X/Y failed points recovered` on rate-limited minutes. Rollback: revert the commit and apply again. Open: the second pass adds up to ~15 s to a run only when points failed, so watch the ingest duration and the Lambda GB-seconds (74% of the free allowance was used in the 10-04 audit).
