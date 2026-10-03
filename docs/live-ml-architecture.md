# Live trajectory prediction: architecture, decisions and risks (2026-09-26)

> **Status, 2026-10-03.** This is the design written on 2026-09-26. Built and live: the ingest Lambda writing the live JSON files, the
> predict Lambda (ONNX GRU, shared `ml/features.py`), the live evaluation loop (`metrics/`, `eval_log/`), the API endpoints and the dashboard's
> click-to-focus panel. **Not built as designed:** CloudFront static JSON (CloudFront is not allowed on this account, so the browser polls the API),
> the MapLibre/PMTiles map (the dashboard uses Leaflet), jitter/gap augmentation in training, and automated weekly retraining (retraining is a
> manual Colab run). The per-decision status is tracked in the D1-D25 table of [liveflights-ml-journal.md](liveflights-ml-journal.md).

Single source of truth for how the pieces connect. Every number marked *(est.)* is an estimate that
must be measured before it is relied on. Facts marked *(verified)* were read from code or measured
in a log.

## 1. Goal
For every aircraft on the live map, predict its position for the next 5 minutes, show the
prediction next to what really happened, and make this smooth and professional for any visitor.
Clicking an aircraft fades all others and shows only that flight: its real trail, the predicted
path (curved when it is turning), how wrong the last predictions were, its route and its details.
It must keep working for at least the next 30 days without manual work.

## 2. Current state (verified from code)
- Ingest Lambda: zip, stdlib+boto3 only, 256 MB, 90 s timeout, runs every minute (EventBridge). It
  polls adsb.lol at 8 hub points (250 nm each), merges by ICAO, maps **16 of ~50 fields** and drops
  the rest, sends NDJSON to Firehose (60 s buffer, GZIP) -> `bronze/`, and overwrites
  `live/latest.json` and `stats/hourly.json`.
- S3 lifecycle deletes `bronze/` after **30 days**.
- API: FastAPI container Lambda (512 MB) behind API Gateway HTTP API with a **5 req/s, burst 10**
  throttle; reads `live/latest.json` and computes stats per request.
- Frontend: Next.js 14 static export, React-Leaflet with one **DOM marker per aircraft**
  (`AircraftLayer.tsx`, designed for ~150 aircraft), an existing `GhostTrailLayer` (trail + ONE
  predicted point), Recharts. Live data is ~2,500 aircraft per poll.
- adsb.lol: free, "contact the owner for production use", ODbL licence; HTTP 429 seen in 5 of 8
  circles in one test.

## 3. Target data flow
```
adsb.lol API (8 points, every 60 s)
  -> INGEST Lambda ----> S3 raw/   (every field, lossless, gzip NDJSON, ~1 object per minute)
                    \--> S3 live/positions (slim, for the map)
                    \--> S3 live/history   (last 60 min per aircraft, only what the model needs)
  -> PREDICT Lambda (ONNX, triggered right after ingest)
        reads history + model, writes live/predictions, live/pending (last 6 min of predictions),
        and evaluation rows (prediction made k min ago vs actual now)
  -> CloudFront (static JSON, 10-60 s cache) -> Dashboard
  -> DETAIL API (Lambda Function URL, cached per aircraft) -> click-through panel
daily batch (GitHub Actions, free) : raw/ -> silver Parquet (training schema) -> Colab training
                                     -> ONNX + normalisation stats -> S3 models/ (versioned)
```

## 4. Data capture: OPTION B (decided 2026-09-26; see liveflights-ml-journal.md sections 5 and 7)
CORRECTION: an earlier version said "time-critical, data can never be re-created". That was wrong:
GitHub keeps every day with all fields. Only the live-format data (our jitter, dropouts, coverage)
cannot be recreated. Decision: read ALL fields in the ingest Lambda, keep a 60-min history, predictions,
pending, permanent metrics and a ROLLING 7-DAY raw archive; retrain weekly from GitHub.
- Fields to keep from each aircraft: hex, flight, r, t, type, lat, lon, alt_baro, alt_geom, gs, ias,
  tas, mach, wd, ws, oat, tat, track, track_rate, roll, mag_heading, true_heading, baro_rate,
  geom_rate, squawk, emergency, category, nav_qnh, nav_altitude_mcp, nav_altitude_fms, nav_heading,
  nav_modes, nic, rc, nac_p, nac_v, sil, sda, gva, version, alert, spi, seen, seen_pos, mlat, tisb.
  Not needed: messages, rssi, dst, dir. Omit null keys to save bytes. Add poll_ts and point id.
- Write directly to S3 (one gzip NDJSON object per minute) instead of Firehose: Firehose bills the
  ingested bytes (~$0.029/GB), so ~2.5-3.5x more bytes would raise it from ~$1.1 to ~$3-4/month
  *(est.)*; direct PUT is ~$0.22/month + storage *(est.)*. Small objects are compacted per hour.
- Keep `live/positions` slim: the map must not download the 50 fields.
- Lifecycle: raw 7 days (Option B), metrics permanent, frozen test sets copied out before expiry.
- Old bronze (16 fields) stays as the serving-like test set for the core-field model.

## 5. Training data: two sources, ONE schema
- GitHub-derived (already extracted, 10 s grid, seasons and history) and live-derived (native 60 s,
  from raw/) must produce the same window tensors, otherwise the model trained on one behaves
  differently on the other (train/serve skew).
- Decision: one shared feature module used by the builder AND the Lambda, taking arrays of history
  and returning X and S. No feature code is duplicated.
- Per-step feature `dt_min` (time since previous reading) is part of X. Grid data has dt = 1.0.
  Live data has jitter (position age `seen_pos`) and missing polls (429, coverage), so dt and
  masks must exist from the start, plus augmentation (random jitter, dropped steps) in training.
- Field names/units must be identical (m, m/s, deg) and documented once.

## 6. Model
- Input: last 10 readings at 60 s, 12 core features (position change in km, speed, track sin/cos,
  vertical rate, altitude, missing flag, per-step changes, dt), 10 extra values and 9 masks (roll,
  track_rate, heading-minus-track, mach, tas, ias, wind, autopilot altitude/heading deltas),
  static features (position, hour, weekday, ADS-B category, wake class, helicopter, military).
- Output (primary, available from BOTH sources): position at +1..+5 min, plus track and speed at
  the same marks. The dashboard draws a smooth path with a cubic Hermite spline through the
  predicted positions and headings, so a turn looks like a turn.
- Auxiliary (GitHub-derived data only, masked when absent): dense 10 s targets (30 points).
- Baselines to beat, always on the same windows: straight line AND constant turn rate.
  Report separately for level cruise / turning / climbing-descending, and for unseen aircraft.
- Training mix: 40% uniform, 40% hard cases, 20% turning/vertical; val and test are uniform.
- Extra fields are validated against physics BEFORE training (`validate_extras.py`), then an
  ablation (core only vs core + extras) shows whether they help.
- Acceptance before going live: better than the best physics baseline on hard cases, not worse on
  level cruise, on val, on test days and on unseen aircraft.

## 7. Serving
- Separate PREDICT Lambda (ONNX Runtime needs numpy: use a layer or container image), 1 GB,
  a few seconds per minute for ~2,500 aircraft *(est.)*. Ingest stays small and reliable.
- `history` (last 60 min, ~15 numbers per reading) is one S3 object rewritten each minute
  (~4 MB gzipped *(est.)*); `pending` keeps the last 6 minutes of predictions.
- Live evaluation is automatic: each minute compare the prediction made k minutes ago with what
  happened, store per-aircraft errors and hourly aggregates (median, p90, by regime, vs both
  baselines). This is the 30-day proof.
- Graceful degradation: if the model or history is missing for an aircraft, fall back to the
  physics baseline and label it; if predict fails, positions still update.
- Predictions may extend outside the covered area; the "actual" line stops at the coverage edge.

## 8. Delivery to the browser
- Hot path = static JSON on CloudFront with ETag and 10-60 s cache; no Lambda per visitor.
  (API Gateway throttle of 5 req/s would break with ~70 visitors.)
- Positions as compact arrays, not objects (~150 KB, ~70 KB gzipped *(est.)*).
- Detail endpoint (per aircraft, cached): route, airline, type, registration, current state with
  all fields, last 60 min trail, last predictions vs actual, per-aircraft error.

## 9. Dashboard behaviour (spec)
- Map: WebGL (MapLibre GL) with own vector tiles (PMTiles on S3) because ~2,500 DOM markers and
  the public OpenStreetMap tile servers do not scale (usage policy). Attribution: OpenStreetMap,
  adsb.lol (ODbL), Virtual Radar Server data (CC0, credit requested).
- Smooth motion: extrapolate each aircraft between updates, blend when new data arrives.
- Click an aircraft: all others fade to a faint dot; show its real trail (solid), the current
  prediction (dashed, curved, with an error cone that grows with time), the prediction made 5 min
  ago vs where it actually is (the visible "how wrong were we"), origin -> destination arc with
  airports (VRS routes/airports, marked as scheduled route), progress, and a panel: airline,
  aircraft type/wake class, registration, speed, altitude, vertical rate, roll, mach, wind,
  autopilot targets, position source, data age. A small chart of prediction error over time.
  Esc / click on empty map restores everything. Deep-linkable URL per aircraft.
- Loading skeletons, "data is N s old" badge, keyboard and touch friendly, works at phone width.
- Later: "focus mode" for the selected aircraft polls that one aircraft faster (per-hex endpoint,
  through a cached proxy) and uses a 10 s model for sharper turns.

## 10. Cost model *(est., verify with Cost Explorer usage type, not net of credits)*
| Item | Estimate |
|---|---|
| Ingest Lambda 256 MB x ~10 s x 43,200/month | ~110k GB-s (free tier 400k) |
| Predict Lambda 1 GB x ~3 s x 43,200/month | ~130k GB-s |
| Raw objects PUT (43,200/month) + S3 storage (~20 GB/month kept 90 d) | ~$1-2 |
| Firehose (if kept, all fields) | ~$3-4 (avoid) |
| CloudFront | inside always-free tier (verify) |
| API Gateway (detail only, cached) | cents |
| Total AWS | roughly $2-4/month, covered by credits |
Budget alert ($5, warns at $2) must be raised when this ships.

## 11. Risks and answers
| Risk | Answer |
|---|---|
| adsb.lol rate limits (429) and terms | Measure dropout first (`check_data_quality.py`); backoff/jitter; contact the owner about production use; keep positions working if a region drops |
| Missing minutes in history | dt + masks + augmentation; fall back to physics when <N readings |
| Train/serve skew | one shared feature module; replay recorded live data through the serving code and compare with the builder output |
| Lambda overlap / slow polls | ingest time budget << 60 s; idempotent keys per minute; alarm on data age |
| Cold starts on the detail API | zip Lambda without numpy, Function URL + CDN cache; no container on the hot path |
| Map slowness | WebGL, canvas-free DOM, viewport culling, updates by ETag |
| S3 small-object explosion | hourly compaction job |
| Model drift (season, schedule change on 25 Oct) | daily live evaluation, retrain on live + GitHub data, keep last good model, canary/shadow before switching |
| Sign/unit mistakes in extra fields | `validate_extras.py` before training |
| Licences | ODbL attribution + share-alike for published derived data; OSM tile policy |

## 12. Order of work (dependencies)
1. Raw all-fields capture (7-day rolling, Option B) in the ingest path (needs approval, ~$1-2/month);
   measure live dropout; contact adsb.lol.
2. Validate extras, finish the window builder (dt, headings, shared feature module), build the
   dataset, baselines table.
3. Train on Colab (GRU, ablation), decide.
4. Export ONNX, predict Lambda, history/pending/eval objects, replay test against recorded data.
5. Static JSON + CDN, detail endpoint.
6. New map + focus UX.
7. 30-day live evaluation, README/portfolio, monitoring (data quality, drift).

## 13. Not verified yet
- Whether readsb `wd` means wind FROM or TO, sign of roll/track_rate (validate_extras.py).
- Real 429 dropout rate of the deployed Lambda (check_data_quality.py v3).
- Firehose vs direct-S3 cost numbers, CloudFront free-tier limits, Lambda durations.
- adsb.lol `/api/0/routeset` quality (VRS routes are the planned source).
