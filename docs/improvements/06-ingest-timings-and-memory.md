# 06 · Ingest Lambda: see where the 18 seconds go, and give it more CPU

Status: **code written and tested, needs the same `terraform apply` as step 07; results to be filled in after one hour of
data** (this document does not claim a speed-up yet)

## Situation

The ingest Lambda runs every minute and takes a long time for what it does. Last 24 h from CloudWatch (1,440 invocations,
0 errors, 0 throttles): average **18.3 s**, p50 18.1 s, p95 25.9 s, p99 35.0 s, max 48.4 s, memory used 157 MB average and 203 MB
maximum of 256 MB. It has never been broken down: the work is 8 parallel adsb.lol fetches (with 429 retries and
staggering), a Firehose write, a 1.6 MB `latest.json` write, a small hourly-stats read-modify-write, and a ~10 MB
`history.json` read, parse, trim and write back. Lambda CPU scales with memory, and at 256 MB a 10 MB JSON round trip is
slow, but a long network wait on adsb.lol would not be helped by more CPU. Without measurement either explanation is a guess.

## Task

Measure each phase inside the function, then change memory and see what moves. Keep the change reversible and the cost
visible.

## Action

- `handler.py` times each phase with `time.monotonic()` and writes one greppable line per run:
  `PHASE_TIMINGS_S {"fetch":..,"firehose":..,"latest_json":..,"map_json":..,"hourly":..,"history":..,"total":..} aircraft=N`.
- `lambda_ingest.tf`: `memory_size` 256 to 512 (more CPU and more headroom; the 203 MB peak was at 79 % of the old limit).
- Query to read the result (CloudWatch Logs Insights, log group `/aws/lambda/liveflights-prod-ingest`):
  `filter @message like /PHASE_TIMINGS_S/ | parse @message 'PHASE_TIMINGS_S * aircraft' as t | stats count() by bin(1h)`
  and for the split, `parse @message '"fetch": *,' as fetch` (and the same for `history`, `latest_json`), then `avg()`/`pct(.., 95)`.
- Duration and memory summary: `filter @type="REPORT" | stats avg(@duration), pct(@duration,95), avg(@maxMemoryUsed/1048576) by bin(1h)`.

## Result

Pending the apply. What will count as a result: the same REPORT summary for the hour after vs the 18.3 s / 25.9 s / 157 MB above,
and the phase split showing whether `fetch` (network wait, memory-independent) or `history` (CPU) dominates. If `fetch`
dominates, memory 512 will not help much and the right next change is the adsb.lol retry/stagger budget or storing the history
object smaller; that would be step 06b.

**Cost note.** Lambda is billed in GB-seconds: 512 MB at half the duration costs the same as 256 MB at the old duration; if the duration does
not fall, the bill doubles for this function (1,440 runs/day x 18 s x 0.25 GB = 6,480 GB-s/day at 256 MB, inside the free tier of 400,000
GB-s/month at either size, so the dollar impact is about zero either way; see [01](01-aws-cost-audit.md)).

**Rollback:** set `memory_size` back to 256 and apply; the timing line is harmless and can stay.
