# 09. Train/serve skew: wake class and military flag (2026-10-07)

## Situation
The model is trained on 14 static features per aircraft. Two of them, `wake_id` (light/medium/heavy from the aircraft type code) and `is_mil` (military ICAO address
block), come from the VRS standing data in training (`ml/scratch/build_windows_v2.py`, `static_tables`). The predict Lambda could not look them up, so
`_static_meta` in `predict/handler.py` sent `wake_id = -1` and `is_mil = 0` for **every** aircraft. The model therefore saw different inputs live than in training:
a train/serve skew, in a project whose docs say the shared `ml/features.py` makes that impossible.

Two related claims were also not true:
- `ml/features.py` and the model card say the serving code refuses a model trained with a different `FEATURES_VERSION`. Nothing checked it, and the deployed
  `trajectory_norm.json` does not even record a version.
- The history already carried `type_code` per aircraft (written by the ingest Lambda), so the data needed for the fix was already in S3.

## Task
Live serving builds `wake_id` and `is_mil` the same way training does, provably, and a feature-version mismatch is caught instead of silently ignored.

## Action
- `ml/static_meta.py`: small stdlib-only module (vendored into the image like `features.py`) with `wake_id(type_code)` and `is_military(icao_hex)`, using the same mapping and
  the same `-1` for unknown as training.
- `scripts/build_static_meta.py` builds `predict/static_meta.json` (38 KB: 2,841 type codes, 414 military ranges) from the VRS data, with the same rules as the training loader.
  A JSON file in the image instead of the 468 KB of CSVs keeps the image small and the cold start fast.
- `predict/handler.py`: `_static_meta(category, type_code, icao)` uses the lookup. `_check_features_version` raises on a mismatch, and warns (today's case) when the norm file has no version.
- `predict/Dockerfile` and `lambda_predict.tf` copy and hash the new files, so `terraform apply` rebuilds the image.
- `tests/test_static_meta.py`: known types, unknown types, military ranges, and (when the VRS data is present) the JSON equals a fresh build.
- `scripts/backtest_static_meta.py`: the before/after measurement below.
- Rejected: retraining. The model was trained with the correct values, so only serving was wrong.

## Result
Measured with `scripts/backtest_static_meta.py` on one live `history.json` snapshot (2026-10-07 ~12:05 local). Each aircraft is predicted 5 minutes ahead from a window that ends
5 minutes before its newest reading, then compared with that newest position. Same model, same windows, only the two features differ.

| 2,017 aircraft | median | mean | p90 |
|---|---|---|---|
| old (`wake=-1`, `mil=0`) | 1.774 km | 4.079 km | 10.73 km |
| new (VRS wake + military) | 1.759 km | 4.064 km | 10.95 km |
| naive straight line (last speed and heading) | 2.762 km | 7.397 km | 20.57 km |

- **The fix is correct but its effect is small:** about 1% better median, 0.4% better mean, p90 slightly worse; better for 47.9% of aircraft, worse for 49.5%. Military aircraft
  (15 in the sample) improved most (median 7.1 to 5.7 km), but that is too few to conclude anything. **It is not the cause of the accuracy drift** seen on 10-06
  (median 2.3 km vs about 1.0-1.6 earlier), so that still needs its own investigation.
- **New, useful number:** the model beats the naive straight-line guess by 36% (median) and 45% (mean) on the same aircraft. This is the baseline comparison the live
  accuracy page was missing.
- Caveat: one snapshot, one time of day, 73% of aircraft eligible. It shows the size of the effect, it is not a daily metric.

Verify: `aws s3 cp` the history and model (see the script header), run the script. Tests: `pytest tests/test_static_meta.py`.
Roll back: revert the commit and re-apply; behaviour returns to `-1`/`0`.

**Still open / needs `terraform apply`:** the new image is not deployed yet. The next model export should write `features_version` into `trajectory_norm.json` so the guard can actually
refuse a mismatch. After deploy, watch `metrics/daily.json` for a few days (expect no visible change at this effect size).
