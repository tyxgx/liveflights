# Trajectory model card (GRU, served as ONNX)

## What it does
For every aircraft on the live map it predicts where the aircraft will be for the next 5 minutes: 30 positions (every 10 seconds, east/north
in km relative to the last reading), plus the track (sin, cos) and ground speed at +1, +2, +3, +4 and +5 minutes. The network learns a
**residual over a straight-line physics guess**, so it only has to learn what physics gets wrong: turns, climbs and speed changes.
The headings and speeds are what let the dashboard draw a smooth, curved predicted path.

## Files
| File | Size | Where |
|---|---|---|
| `trajectory.onnx` | 1.4 MB | `s3://liveflights-prod-lake-922120357133/models/trajectory.onnx` and the GitHub release `model-2026-09-28` |
| `trajectory_norm.json` | 1.3 KB | next to the model: normalisation statistics and flags (`use_extra`, `use_dest`) |

## Interface
ONNX opset 17 (read from the file on 2026-10-03). Inputs (raw, un-normalised; normalisation, the baseline and the residual are inside the graph):

| Input | Shape | Meaning |
|---|---|---|
| `X` | (n, 10, 31) float32 | the last 10 readings, about 60 s apart: 12 core features, 10 extra values and 9 masks (`ml/features.py`: `window_x`) |
| `S` | (n, 14) float32 | static features: last position, hour and weekday (sin/cos), ADS-B category, wake class, helicopter, military, plus 4 destination features (`window_s` and `S_NAMES_DEST` in `ml/features.py`) |

Outputs: `pos` (n, 30, 2), `hdg` (n, 5, 2), `spd` (n, 5). `X` and `S` must be built by `ml/features.py`: the same code trains and serves the
model, and the predict Lambda checks `features_version` in `trajectory_norm.json` against `FEATURES_VERSION` (the currently deployed norm file has no version yet, so today it only logs a warning).

```python
import numpy as np, onnxruntime as ort
sess = ort.InferenceSession("trajectory.onnx", providers=["CPUExecutionProvider"])
pos, hdg, spd = sess.run(None, {"X": X.astype(np.float32), "S": S.astype(np.float32)})
```

## Training data
adsb.lol's public `globe_history` releases on GitHub: full-field ADS-B traces, Europe box, airborne, fresh, ADS-B-sourced points only, resampled
to a 10 second grid. Splits are by **day** and by **aircraft** (20% of aircraft held out entirely), so the test numbers measure generalisation to
days and aircraft the model has not seen. The training mix is 40% uniform, 40% hard cases and 20% turning or climbing windows; validation and test are
uniform. Rebuilding everything from public data: [reproducing-the-model.md](reproducing-the-model.md).

## Results
- **Ablation (2026-09-26, 25% of the training data, 8 epochs, same test subset):** adding the extra fields and their masks (variant B) beat the
  core-only variant A by 5.7% in mean error at +5 minutes on the test set (3.69 km to 3.48 km mean, 1.03 km to 1.02 km median; turning aircraft 6.99 km to 6.54 km mean).
  The full table is in the 17:05 ablation entry of the [ML journal](liveflights-ml-journal.md). The deployed model is variant B with destination features,
  trained on more data; its own training reports are in the journal as well.
- **Live evaluation:** every minute the predict Lambda compares predictions that have come due with the closest actual reading and logs one record per
  prediction (`eval_log/`) and a daily aggregate (`metrics/daily.json`). From that aggregate (`sum_km / n`): 2026-10-01: 25,775 predictions
  evaluated, mean error 4.43 km; 2026-10-02: 23,063 predictions, mean error 4.55 km. These cover every aircraft and every horizon up to 5 minutes, so they are
  not directly comparable with the +5-minute test numbers above.

## Limits
- Europe only, ADS-B only, trained on days in September 2026; behaviour outside that is untested.
- It has no knowledge of air-traffic-control clearances or flight plans beyond the scheduled route table, so a sudden turn it cannot see in the last
  10 minutes of history will be missed.
- icao24 addresses are occasionally reused by two different aircraft within minutes; the evaluation drops matches that imply impossible speeds instead of
  counting them as model error (`_eval_pending` in `predict/handler.py`).
- Free ADS-B feeds rate-limit and drop readings; the model was trained with gaps and irregular spacing in mind (a `dt_min` feature), but jitter and gap
  augmentation during training is still open.

## Attribution
Training data: adsb.lol (ODbL). Scheduled route table: Virtual Radar Server data (CC0, credit requested). If you redistribute work derived from the
adsb.lol data, check the ODbL attribution and share-alike terms.
