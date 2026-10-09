# 10. Accuracy drift investigation: the metric was lying, not the model (2026-10-07)

## Situation
The live dashboard showed the daily median error jump from about 1.0-1.6 km to **2.35 km on 2026-10-06**. The CV and mails quote "median error ~1-1.5 km". Was the model getting worse?

## Task
Find out whether the model drifted or the measurement moved, and make the number honest either way.

## Action
Downloaded every `eval_log/*.json` (234,157 evaluated predictions, 09-28 to 10-07) and recomputed the numbers by day, hour, route known/unknown and timing offset.
Three findings, each checked against the data:

1. **The dashboard's "daily median" is the median of the last 2,000 errors of the day, not of the day.** `_update_metrics` kept `sample_p90_km[-2000:]` and took median
   and p90 from it. For every day 09-29 to 10-06 the dashboard median equals the last-2000 median exactly (e.g. 10-06: 1.57 vs 1.57), while the full-day median is higher
   (10-06: 2.68). At 05:00 UTC the last 2,000 are quiet night traffic (easy); at 19:00 they are busy daytime traffic. The 2.35 km I saw on 10-06 was a partial day read in the
   evening. The same day read the next morning shows 1.57 km.
2. **The evaluation compared the prediction with whichever reading was nearest to the target time, up to 45 s away, without correcting for it.** An aircraft at 250 m/s moves
   250 m per second, so a 10 s offset alone adds about 2 km. Error against offset in the real log: 1.61 km median (offset 0-5 s), 2.19 (5-10), 3.59 (10-20), 5.67 (20-30), 9.44 (30-45).
   Share of matches more than 20 s off: 1.6% on 10-03, 8.4% on 10-06 (jittery polling), so part of any day-to-day change is timing noise.
3. **With the timing noise removed, there is little or no drift.** Matches within 5 s, median / mean error: 10-03 1.45/4.01, 10-04 1.58/4.09, 10-05 1.71/4.39,
   10-06 1.80/4.44. A small upward creep (about +0.3 km median over four days) is visible but within the range of traffic mix and cannot be called drift from this data.
   Duplicates and lost updates were checked and are not the cause: no duplicate prediction records, and `metrics/daily.json` counts equal the eval_log counts.

Changes (`predict/handler.py`):
- `_position_at`: the actual reading is moved to the exact target time by flying its own ground speed and track. Validated on 34,189 real consecutive reading pairs: truth
  by interpolation vs corrected position, median error **0.009 km at 5 s offset, 0.038 km at 20 s, 0.056 km at 30 s**; uncorrected it is 1.06, 4.24 and 6.35 km.
- Daily median and p90 now come from a whole-day histogram (0.1 km bins, 501 ints per day, mean unchanged), not the last 2,000.
- `scripts/backfill_daily_metrics.py` rebuilds past days from eval_log (read-only, writes a local file).
- Tests: `tests/test_predict_eval.py`. Also fixed the 10 ruff errors that made the main branch fail lint, and added `boto3` to the dev group so the ingest tests run in CI.
- Rejected: changing the model. Nothing here points at the model.

## Result
Honest daily numbers (whole day, from eval_log, old time matching): median 1.7-2.7 km, mean 4.1-4.7 km, p90 about 10.5-11.6 km. Clean matches (within 5 s): median about 1.5-1.8 km.
On exact-time truth, a live backtest of 1,948 aircraft gives median 1.76 km, mean 3.96 km, p90 9.9 km, against 2.56 / 7.57 / 22.4 km for flying straight on (model better by 31% / 48% / 56%).

**What the CV and mails may say:** "median error about 1.5-2 km, mean about 4 km, for 5-minute-ahead positions, measured continuously on live traffic; about 30% better than assuming the aircraft flies straight."
Do **not** say "1-1.5 km" any more: that came from the last-2,000 effect and low-traffic hours.

Verify: `python scripts/backfill_daily_metrics.py snap/eval snap/daily.json snap/daily.new.json` (needs the downloads in its header). Roll back: revert the commit; `daily.json` keeps both old and new fields.

**Still open / needs `terraform apply`:** the new predict image. After deploy the live metric changes definition (new days use the histogram, with time-corrected actuals), so new days are not directly comparable with the old ones.
Past days on the dashboard still show the old numbers until the backfilled `daily.json` is uploaded (a deliberate manual step, not done). Why polling jitter was worse on 10-06 is not explained yet (ingest takes 18 s on average).
