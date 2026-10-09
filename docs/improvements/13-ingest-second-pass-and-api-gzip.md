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

## Result
Tests: 85 passed, ruff clean. **Not deployed:** needs `terraform apply` (ingest zip + api image, Docker must be running). After deploy, verify: `curl -H 'Accept-Encoding: gzip' -I .../api/predictions` shows `content-encoding: gzip`, the dashboard still loads, and the ingest log shows `adsb.lol second pass: X/Y failed points recovered` on rate-limited minutes. Rollback: revert the commit and apply again. Open: the second pass adds up to ~15 s to a run only when points failed, so watch the ingest duration and the Lambda GB-seconds (74% of the free allowance was used in the 10-04 audit).
