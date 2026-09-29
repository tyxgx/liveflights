# liveflights ML journal: work done, findings, questions and answers, decisions, contracts, roadmap

Written 2026-09-26 (updated as work continues). This is the memory of the project. Read it before
changing any step. The architecture and its reasons are in `docs/live-ml-architecture.md`.
Facts marked *(verified)* come from a log or code; *(est.)* are estimates; *(unverified)* still to check.

How commands are run: the owner runs every command himself, one at a time, through
`bash run_logs/run.sh <name> "<command>"`. The full log is `run_logs/<time>_<name>.log`, a capped copy is
`run_logs/LATEST.txt`, the live log of a running command is `run_logs/CURRENT.log`, and every finished run
adds a line to `run_logs/INDEX.txt`. Chrome tools are never opened without his explicit yes.

---------------------------------------------------------------------------------------------------

## 1. Goal (in his words) and what it means

- "hame aage ki trajectory ... guess" : predict where every aircraft will be in the next 5 minutes.
- "jab bhi banda ek aircraft pe click kare toh saare aur aricraft fade ho jaaye aur sirf uss ek flight ka route dikhe"
- "jahaz mostly seedhi hi line me chalta hai but jab voh mudta ahi toh usko hame dikhana hai... kahan kahan kaise kaise dikh rha hai"
- "predicted ... aur jo uski real trajectory hai voh ham show karenge actual me dashboard par ... for next atleast 30 days voh ache se honi chaiye"
- "live website par bhot zyada smoothly without lag ... industry standard ... kuch atakkna nahi chiaye"
- "ek bhot hi acha UI UX frontend"
- Cost: "kahin paise na lage sab kuch free hona chaiye"; later he allowed AWS credits (about $100) for the extra
  fields and history file.

Consequences: the model must work on LIVE data (one reading per minute from adsb.lol), be evaluated live against
reality, and the whole path (ingest -> predict -> browser) must be cheap, cached and never block.

---------------------------------------------------------------------------------------------------

## 2. Timeline of the work

2026-09-22  Career audit found the biggest CV gap: PyTorch is claimed but used nowhere; Great Expectations,
            statistics/A-B testing and Kubernetes also missing. Idea: add a deep-learning model to liveflights.
2026-09-25  Real-data checks and pipeline:
  - Verified with `seq_all.py` that the local S3 backup (`data/s3-backup-2026-09-19/bronze`, 30 days, 21 Aug to
    20 Sep) has one reading per aircraft per 60 s: 76.9M airborne rows, about 53M overlapping 10+5 windows.
  - `build_gru_windows.py` (v1, 60 s windows from our own S3 data): 783,093 train / 60,352 val / 200,140 test
    windows; straight-line error @+5 min is only median 1.5-1.8 km on real data (the "9 km" in older docs was the
    simulator).
  - Data authenticity audit (`check_data_quality.py`, `validate` by hand against OpenSky): see section 3.
  - Discovered adsb.lol publishes every day's full history as GitHub releases (`globe_history_<year>`), with ~50
    fields; our own Lambda keeps only 16. Survey/map/audit of the tarball layout, then `extract_globe_day.py`.
  - Round 1 extraction (11 days) finished with no errors, 63.7 minutes.
  - VRS standing data (routes, airports, airlines, aircraft, model types, code blocks) downloaded, 58 MB.
2026-09-26  `inspect_extract.py` on all Round 1 data; `validate_extras.py` (found the stale `track_rate` problem,
            fixed in extractor schema 4); window builder v2 written; architecture and this journal written;
            Option B chosen for live data storage.

---------------------------------------------------------------------------------------------------

## 3. Findings (numbers and where they come from)

### 3.1 Our own live data (S3 bronze, 21 Aug to 20 Sep)
- 87,954,691 real adsb_lol rows vs only 80 `simulate_cloud` (simulator fallback) rows, all on 28 Aug *(verified,
  run_logs/2026-09-25_020346_data_quality.log)*.
- Physics self-consistency: distance between consecutive positions / time vs reported speed = median 0.996-0.997,
  98.9-99.2% within +-10% on every day *(verified)*.
- `ingest_ts` is the Lambda start time, not the observation time: position age median -0.4 to -2.1 s, p99 25-37 s,
  about 1% older than 30 s *(verified)*. Use `time_position`.
- Stray points exist (lon -74 on 27 Aug, 37 India rows on 21 Aug) so a Europe box filter is needed.
- Live cross-check on 5 aircraft (adsb.lol vs OpenSky about 11 s later): speed, track, altitude and displacement
  agree; in the 3 circles that answered, adsb.lol saw 775 airborne aircraft, OpenSky 762, 753 in common
  (98.8% / 97.2%) *(verified live, one moment)*.
- HTTP 429 (rate limit) hit 5 of 8 circles in a browser test *(verified)*. How often it happens in the deployed
  Lambda is NOT measured yet (`check_data_quality.py` v3, "sudden drops", has not been run) *(unverified)*.
- 2.3% of positions were `mlat` (computed); the Lambda hard-codes `position_source=0` and drops `type`.
- adsb.lol terms: free, "In the future, you will require an API key which you can get by feeding to adsb.lol",
  "If you want to use the API for production purposes, please contact me". Licence: ODbL.

### 3.2 What adsb.lol gives per aircraft (raw record example, AIC130 over Belgium)
About 50 fields: hex, type, flight, r, t, alt_baro, alt_geom, gs, ias, tas, mach, wd, ws, oat, tat, track,
track_rate, roll, mag_heading, true_heading, baro_rate, geom_rate, squawk, emergency, category, nav_qnh,
nav_altitude_mcp, nav_altitude_fms, nav_heading, nav_modes, lat, lon, nic, rc, nac_p, nac_v, sil, sda, gva,
version, alert, spi, seen, seen_pos, mlat, tisb, messages, rssi, dst, dir. Our Lambda keeps 16 (`map_to_flight_state_dict`).

### 3.3 GitHub `globe_history` (verified by `audit_day.py` on 2026-09-01, `map_tar.py`, `inspect_globe_history.py`)
- Org `adsblol`: 22 repos. Data: `globe_history_2023/2024/2025/2026`, `vrs-standing-data`, `aircraft-data-links-*`
  (ACARS, not used). Software: api, history, tar1090, mlat-server/client, feed, website, infra ...
- One release per day per pod: `prod-0` (~3.2-4.0 GB), `staging-0` (about the same size; docs: "Prod is used, unless
  it is down then staging is used. There are two replicas of prod and one of staging"), `mlatonly-0` (~0.35 GB,
  MLAT positions only, low quality). Not compared byte for byte (order differs). We use `prod-0`.
- Inside one day's tar (3,872 MB on 2026-09-01): acas/ (2 files), heatmap/ (48 files, 900 MB, one per half hour),
  2 LICENSE files, README.txt (last), traces/ (256 folders = last two hex digits of the ICAO id, 78,489 files,
  2,912 MB). Order of the sections differs per day (heatmap first on 09-01, last on 09-15).
- traces/: 249-368 files per folder, 10,721 (14%) are `~` (non-ICAO) addresses, all file names are
  `trace_full_<hex>.json`, gzip JSON, median 20 KB, max 603 KB.
- Inside a file: keys icao, version, timestamp, trace (100%), dbFlags 95%, r 94%, t 93%, desc 86%, ownOp 53%, year 49%.
  Every trace point has exactly 14 elements. `timestamp` is 00:00 UTC of the day; point[0] = seconds after it.
  25% of points carry an "aircraft object" (world-wide: category 98%, flight 96%, alt_geom 88%, nav_altitude_mcp 68%,
  nav_heading 42%, true_heading 39%, ias/mach 31%; Europe traffic reports much more).
- Sampling check: aircraft touching Europe are 23.1% in the first 1 GB of traces, 23.7% in the rest, 23.5% overall,
  so the first 1 GB (about 34% of aircraft, chosen by hex folder) is an unbiased sample; about 15,900 Europe aircraft per day.
- Point spacing: median 4 s per saved point, but by TIME the median gap between the two real points around a grid row
  is about 18 s (readsb saves few points in straight cruise, many in turns).
- Download speed from GitHub: 2-8 MB/s normally; at some hours every NEW request waits about 20 s for the first
  byte (later up to 140 s). Rule: few big requests, never many small ones.
- ODbL licence (attribution, share-alike); LICENSE-cc0 for feeder data.

### 3.4 Extraction result (Round 1, `extract_globe_day.py`, 11 days, exit 0, 63.7 min)
| day | role | Europe aircraft | rows | trace MB | output MB |
|---|---|---|---|---|---|
| 09-01 | train | 4,156 | 6,299,840 | 1,000 | 305 |
| 09-03 | train | 4,096 | 6,290,153 | 1,000 | 306 |
| 09-05 | train | 4,337 | 6,706,855 | 1,000 | 326 |
| 09-07 | train | 4,318 | 6,523,937 | 1,000 | 315 |
| 09-09 | train | 3,947 | 6,146,647 | 1,000 | 304 |
| 09-11 | train | 4,023 | 5,933,354 | 1,000 | 290 |
| 09-13 | train | 4,252 | 6,517,962 | 1,000 | 317 |
| 09-15 | train | 4,112 | 6,088,278 | 1,000 | 292 |
| 09-17 | val | 3,925 | 6,025,425 | 1,000 | 296 |
| 09-20 | test | 4,339 | 6,675,254 | 1,000 | 326 |
| 09-23 | test | 4,165 | 6,082,382 | 1,000 | 293 |
Total 69,290,087 rows, 18,692 distinct aircraft, all 256 hex folders, about 3.3 GB. Checks on all of it
(`inspect_round1`): physics speed check 0.997 / 100% within +-10% over 60M pairs; straight-line error @+5 min
median 1.43 km, p90 16.95 km; 10 s grid exact for 99.4% of consecutive rows.
Field presence (share of rows): alt_geom 99, vrate_baro 97, ias 92, roll 89, track_rate 83, true_heading 92, mach 92,
tas 90, wind 89, nav_alt_mcp 94, nav_heading 58, nav_alt_fms 34, nav_modes 12, nic/nac/sil 99. Share of AIRCRAFT that
ever report: roll 81, track_rate 73, nav_alt 85, nav_heading 62, wind 71, mach 82.
Filters applied: Europe box (lat 35-64, lon -13..33), airborne, ADS-B sources only (`adsb_icao`, `adsb_other`,
`adsr_icao`), not stale, `~` addresses dropped, impossible values set to NaN.

### 3.5 Validation of the extra fields (`validate_extras.py`, 13.2M rows, 2026-09-26)
| check | result | verdict |
|---|---|---|
| roll vs real turn (track change/s) | corr 0.839, sign agree 97.0% | OK: positive roll = right bank = right turn |
| roll vs physics g*tan(roll)/v compared with track_rate | corr 0.793, slope 0.816 | OK |
| wind: ground velocity minus air velocity vs (wd, ws) | corr +0.918 / +0.892 for FROM, -0.918 / -0.892 for TO; medians 20.3 vs 19.5 m/s | OK: `wd` = direction the wind blows FROM; `WIND_FROM = True` is right; units right |
| mach * speed of sound(oat) vs tas | ratio 1.000, corr 0.997 | OK: mach, tas, oat units right |
| ias/tas | 0.933 near ground, 0.570 at cruise | OK |
| autopilot altitude vs vertical rate | agree 93.5% | OK |
| vrate_baro vs real altitude change | corr 0.970, slope 0.978 | OK |
| autopilot heading vs turn direction | agree 65.5% | weak but above 0.5: `nav_heading` only matters in heading mode; keep with mask |
| track_rate vs real turn | corr 0.533, slope 0.295, sign agree 81% | PROBLEM: the extractor HELD track_rate for up to 90 s; a fast-changing value must not be held. Fixed in extractor schema 4 (linear interpolation between object samples, max 45 s apart; true/mag heading interpolated as angles; tas/mach held 30 s). Round 1 must be re-extracted |
| geometric minus barometric altitude | p10 130 m, median 427 m, p90 648 m | information only (`alt_geom` is not a model input) |

### 3.6 VRS standing data (`ml/scratch/download_vrs.sh`, 58 MB, `data/vrs/`, CC0, credit requested)
Upstream `vradarserver/standing-data` (50 MB). Folders: aircraft (665 files: ICAO, Registration, ModelICAO,
Manufacturer, Model, IsPrivateOperator, Operator, AirlineCode, SerialNumber, YearBuilt), airlines (Code, Name, ICAO,
IATA), airports (659 files; bulk `airports.csv` 34,128 rows: Code, Name, ICAO, IATA, Location, CountryISO2, Latitude,
Longitude, AltitudeFeet), code-blocks (Start, Finish, Count, Bitmask, IsMilitary, CountryISO2), countries,
model-type (ICAO, Manufacturer, Model, Engines, EngineTypeCode, EnginePlacementCode, SpeciesCode, WakeTurbulenceCode,
IsActive), registration-prefixes, routes (1,579 files; bulk `routes.csv` 620,700 rows: Callsign, Code, Number,
AirlineCode, AirportCodes like `KONT-KGSO`). Per-callsign JSON is also served at
`https://vrs-standing-data.adsb.lol/routes/<COUNTRY>/<CALLSIGN>.json` (fields: callsign, number, airline_code,
airport_codes, _airport_codes_iata, _airports[name, icao, iata, location, countryiso2, lat, lon, alt]).
Routes are scheduled routes by callsign, not proof of the actual flight.

### 3.7 Existing live system (read from code on 2026-09-26)
- Ingest Lambda: zip, stdlib + boto3 only, 256 MB, 90 s timeout, every minute; 8 hub points x 250 nm, 3 workers,
  retry on 429; Firehose (60 s buffer, GZIP) -> `bronze/ingest_date=/ingest_hour=`; overwrites `live/latest.json`
  and `stats/hourly.json`. Lifecycle deletes `bronze/` after 30 days. Budget alert $5 (warns at $2), gross, not net of credits.
- API: FastAPI container Lambda (512 MB) behind API Gateway HTTP API, throttle 5 req/s, burst 10.
- Frontend: Next.js 14 static export, react-leaflet, one DOM marker per aircraft (`AircraftLayer.tsx`, designed for
  ~150 aircraft), `GhostTrailLayer` (trail plus ONE predicted point), Recharts.
- StreamPulse EC2 `i-05ee8eaa6303d70e0` is stopped (checked 2026-09-25).

---------------------------------------------------------------------------------------------------

## 4. Questions and answers (his questions, then the outcome)

Q: "hame data -- lat long pure europe ko cover kar rhe hain right???"
A: No. A box, lat 35-64, lon -13..33: UK, Ireland, France, Spain, Portugal, Germany, Italy, Greece, Poland, Baltics,
   Romania, Bulgaria, west Turkey, part of North Africa. Not Iceland, north Norway/Sweden/Finland (above 64 N),
   east Turkey, Cyprus, Ukraine/Russia, Canaries/Azores. Chosen because it matches the live pipeline's 8 circles
   (S3 data bounds were lat 35.8-63.2, lon -8.9..32.7).

Q: "jo aircraft b uss box se bahar jaa rhe hain ... unka toh prediction ham kar hi nahi paayenge right?"
A: The model does not know the box; the box only limits training/live data. Prediction can be drawn beyond the edge,
   "actual" stops at the coverage edge. Windows near the edge lose their future in training, so a margin of about 3
   degrees beyond the serving area is advisable (open item). A newly seen aircraft has no 10-reading history: physics fallback.

Q: "Sirf 50 chune hue din...isse ho jaeyga na sab kuch ache se trian???"
A: Enough for a first model, measured with a learning curve (8 -> 25 -> 50 days); risk = one season and rare events.

Q: "alternates kyu le rhe hian ham?? ... agar jis din ka ham nahi le rhe ..."
A: Neighbouring days are near-identical; alternates only save time. Recent 4 weeks all days, older days sparser.

Q: "yaar 10 second wali usme seekhega lekin...jahan plane mudta hai voh toh bhot sensitive jagah hai na ... same tareeke se dikkat nahi hogi kuch?"
A: Yes, it could. Input spacing stays 60 s (the live pipeline gives one reading per minute; training on 10 s input would
   create train/serve skew). Fine turn detail comes from (1) instantaneous fields roll/track_rate/nav_heading, (2) dense
   10 s targets as an auxiliary signal, (3) heading + speed targets so the path can be drawn as a curve, (4) the
   constant-turn-rate baseline, (5) oversampling turning/hard windows and reporting by regime. Full plan in section 8.

Q: "ye 15 frames per minute, 4 seconds me agar hame data mil rha hia voh toh aur bhi zyada accurate ..."
A: Denser is more detail, not more accurate per point. Live has 1 reading per minute, so train on 60 s spacing;
   use the dense data for phases (6 offsets), exact targets and as an auxiliary target.

Q: "yeh toh yaar yeh sab around 50% jahaj aise bhejte hain... training ke time par sab kuch ache se train hi nahi ho payega"
A: Core features (position, speed, track, altitude) exist for ~100%; extras are a bonus. Missing fields are 0 with a mask;
   ablation core-only vs core+extras proves value. Live serving has the same 50% pattern, so training matches it.

Q: "test haamre s3 wale data pe hi karenge mltb??? usme toh aadhe se zyada featrures hai bhi nahi!!!!"
A: Right. Old S3 data can only test a core-field model. The extras model is tested on held-out GitHub days.

Q: "model B ofcourse better hoga!!..ham log model A par train test karke time waste kyu karein???"
A: Not obvious (missing fields can add noise). A is only a control run of the same code with extras hidden (5-10 min).
   The served model is B. "A/B" = compare two versions changing one thing.

Q: "bhai 55 din?? wtf???"
A: 55 days of DATA (about 3 hours of downloading), not 55 days of work. Round 1 = 11 days first.

Q: "yaar toh fir toh trajectories yeh log already de hi rahein hain na...sab kuch...heatmap ka mtlb kya exactly?"
A: They give history (next day), not live data, not predictions, not a dashboard. Heatmap = readsb "each aircraft at most
   every interval seconds" (default 30 s) binary files for density/replay; redundant with traces.

Q: "aur please ek baar mere liye ... exactly hai kya kya github par..."
A: Inventory in section 3.3 and `docs` plan. Only `traces/` of `prod-0` is used, plus VRS data.

Q: "ek baar prod aur traces ke andar ka structure pata hai exactly tujhe?????"
A: Not exactly at that time; `audit_day.py` streamed a full day and gave the exact structure (3.3).

Q: "aur ek aur baat ham data ... kya kya uthane hai...jo 35 fields phenk dete hain..."
A: Tiers: instantaneous turn (track_rate, roll), autopilot intent (nav_altitude_mcp, nav_heading, nav_modes), dynamics
   (mach, tas, ias, wind, category, type), quality (nic, nac, sil, type). Coverage measured in 3.4; validated in 3.5.

Q: "yaar yeh toh heavy mistake hogya ha, logo ne yeh data save nahi kiya hai!!!!... kahin kisi repo me ya kaggle ya hugging face"
A: Found: adsb.lol's own GitHub history. Hugging Face `AirsideLabs/ADSB` and Kaggle `ADS_B_dataset` were not opened.

Q: "yeh sab live website par ... lekin jo ham use kr rhe hian voh kya kya hai?"  (features actually used in the GRU)
A: 5 core signals today; the v2 builder uses 12 core + 10 extra + 9 masks + 10 static features per window (section 6).

Q: "bhai jab koi command dega na mujhe toh ONLY ONE COMMAND AT A TIME DENI HAI"
A: Rule saved in memory: one command per turn.

Q: "tujhe agar logs read karne me time lag rha hai toh ek limit laga de..."
A: `run.sh` writes `LATEST.txt` (capped), and now `CURRENT.log` (live).

Q: "chrome sirf meri confirmation ke baad hi kholna hai"
A: Saved as a bold memory rule.

Q: "ab ek baat bata..jo aircraft b uss box se bahar ..." see above.

Q: "sabse pehle toh sara data lo... recent bhi lelena s3 se..."
A: S3 recent (20-25 Sep) synced to `data/s3-recent/bronze`; the 21 Aug-20 Sep backup is kept (S3 expired it after 30 days).

Q: "ya ham aisa bhi kar skte hain ki data ko rakhein hi na S3 me... 6-7 din me ek baar naya data github se download krke ... retrain"
A: Mostly yes; correction: history is NOT lost (GitHub keeps every day, all aircraft, all fields), so raw archiving is not
   time-critical. What GitHub cannot give is data in OUR live format (jitter, dropouts, 8-circle coverage). Decision:
   Option B (7-day rolling raw archive, about $0.3/month) plus weekly retraining from GitHub. See section 7.

Q: "B se kaam karenge...tujhe pura conviction hai na B par...ek baar ache se check karna ki koi bhi aage ka step tute na"
A: Dependency check in section 7.3.

---------------------------------------------------------------------------------------------------

## 5. Decisions register

| # | Decision | Why | Status |
|---|---|---|---|
| D1 | Train on GitHub `prod-0` traces (first ~1 GB of `traces/` per day, ~34% of aircraft, unbiased) | full fields, no 429 gaps, all seasons, free | done for Round 1 |
| D2 | Europe box lat 35-64, lon -13..33 (+ margin later) | matches live circles | open: add margin |
| D3 | Only airborne, ADS-B (no MLAT), non-stale, non-`~` points | quality | done |
| D4 | 10 s grid stored, 60 s windows made from it, 6 phases | live = 60 s; phases add variety | done |
| D5 | Model input at 60 s spacing, 10 steps | serving constraint | done in builder |
| D6 | Targets: position + heading + speed at +1..+5 min (primary); dense 10 s positions (auxiliary, masked when absent) | both sources have the minute marks; heading enables a smooth path | done in builder |
| D7 | Baselines: straight line and constant turn rate | fair, no fake wins in turns | done in builder meta |
| D8 | Train mix 40% uniform / 40% hard (straight error > 3 km) / 20% turning-or-vertical; val/test uniform | balance without bias in evaluation | done |
| D9 | Splits by day (train 8 days, val 09-17, test 09-20 and 09-23) and by aircraft (20% of hex folders held out) | honest generalisation | done in builder |
| D10 | Extras are inputs with masks; ablation A (core only) vs B (core + extras) | prove value | planned |
| D11 | Validate every extra field against physics before training | wrong sign/unit poisons a model | done (3.5) |
| D12 | Fast-changing object fields are interpolated, not held (schema 4) | track_rate held 90 s was stale | code done, re-extraction needed |
| D13 | `dt_min` feature and jitter/gap augmentation | live spacing is irregular | dt_min in builder; augmentation planned |
| D14 | One shared feature module for builder and Lambda | prevents train/serve skew | planned (must exist before serving) |
| D15 | Live storage = Option B: no long archive; rolling 7-day raw (all fields, gzip) + history + predictions + permanent metrics | cost, simplicity, but keeps debug/parity | chosen 2026-09-26 |
| D16 | Retrain about weekly from GitHub (extract new days, build, Colab) | live archive not needed | planned |
| D17 | Separate PREDICT Lambda (ONNX), ingest stays small | reliability, package size | planned |
| D18 | Hot path to browser = static JSON on CloudFront; API Gateway only for cached detail | 5 req/s throttle, cold starts | planned |
| D19 | Map = MapLibre GL (WebGL) with own vector tiles; smooth extrapolation between updates | Leaflet DOM markers do not scale; OSM tile policy | planned (owner's yes pending) |
| D20 | Click on aircraft: others fade; trail, curved prediction, past prediction vs actual, VRS route, details | product requirement | spec in architecture doc |
| D21 | Routes/airline/aircraft names from VRS data (CC0) | free, static | data downloaded |
| D22 | Contact adsb.lol about production use; show attributions (adsb.lol ODbL, OSM, VRS) | terms | pending (owner) |
| D23 | Measure live 429 dropout (`check_data_quality.py` v3) | decides gap handling | pending run |
| D24 | Old S3 bronze (16 fields) is kept as the serving-like test set for the core-field model | only real live-format data we have | keep, do not delete |
| D25 | AWS spending must stay small; credits allowed; raise the $5 budget alert when shipping | owner preference | pending at deploy |

---------------------------------------------------------------------------------------------------

## 6. Data contracts (what each step produces and the next step relies on)

### 6.1 Extraction output (`data/globe_extract/<YYYY-MM-DD>_pNN.parquet`, schema 4)
Columns (41): ts (epoch s, float64), lat, lon (float64), alt_baro_m, alt_geom_m, gs_ms, track_deg, vrate_baro_ms,
vrate_geom_ms, ias_ms, roll_deg, gap_s, track_rate (deg/s), true_heading, mag_heading, mach, tas_ms, wd_deg (FROM),
ws_ms, oat_c, tat_c, nav_alt_mcp_m, nav_alt_fms_m, nav_heading, nav_qnh, nic, nac_p, nac_v, sil, sda, gva, rc,
squawk, nav_modes (bitmask autopilot=1, vnav=2, althold=4, approach=8, lnav=16, tcas=32), emergency_flag, icao,
dir2, type_code, category, callsign, db_flags. Units: metres, m/s, degrees. Grid: 10 s. NaN is stored as NULL.
`<day>_aircraft.parquet`: icao, dir2, reg, type_code, desc, operator, year, db_flags, callsign_first, rows.
`<day>.done.json`: statistics and `schema` (a day with a different schema is redone).

### 6.2 Windows (`data/gru_v2/<split>/<day>/`, `dataset.json`)
X (n,10,31) float32: 12 core [dx_km, dy_km, gs_ms, sin_trk, cos_trk, vrate_ms, alt_m, vrate_missing, dtrack_deg,
dgs_ms, dalt_m, dt_min] + 10 extra values [roll_deg, track_rate, hdg_minus_trk, mach, tas_ms, ias_ms, wind_u,
wind_v, navalt_minus_alt, navhdg_minus_trk] + 9 masks [m_roll, m_trate, m_hdg, m_mach, m_tas, m_ias, m_wind,
m_navalt, m_navhdg]. Ablation A = first 12 columns only. S (n,10): lat, lon, hour_sin, hour_cos, dow_sin, dow_cos,
category_id, wake_id, is_heli, is_mil. Y (n,30,2) km east/north at +10..+300 s; YH (n,5,2) sin/cos of track at
+1..+5 min; YV (n,5) speed. `meta.parquet`: icao, day, ts_last, unseen, kind, err5_straight_km, err5_ctr_km,
gap_max_s, regime, type_code, category, wake, is_mil. Baselines are exact functions of X.

### 6.3 Live S3 objects (Option B; names are proposals)
`raw/ingest_date=/ingest_hour=/<HHMMSS>.ndjson.gz` (all fields, expires in 7 days),
`live/positions.json` (slim, for the map), `live/history.bin.gz` (last 60 min per aircraft, model fields),
`live/predictions.json`, `live/pending.json` (last 6 min of predictions), `metrics/<date>.jsonl` (permanent, small),
`models/<version>/{model.onnx, norm.json, features.json}` and `models/current.json` (pointer), `frozen/` (copies of
raw days kept as test sets).

### 6.4 Versioning rule
`features_version` is written into `dataset.json` and into every model's `features.json`; the predict Lambda must
refuse a model whose version differs from its own shared feature module.

---------------------------------------------------------------------------------------------------

## 7. Live data storage: Option B and the dependency check

### 7.1 What B is
No long raw archive. The ingest Lambda reads ALL the fields it needs from the live API (needed for serving in any
option) and keeps: a 60-minute `history`, predictions, `pending`, permanent small `metrics`, and a rolling 7-day raw
archive. Weekly retraining takes new days from GitHub.

### 7.2 Why B (and what was wrong earlier)
Earlier claim "data will be lost, time-critical" was wrong: GitHub keeps all days with all fields, so training data can
be re-created at any time. What GitHub cannot reproduce is OUR live format (poll jitter, dropouts, 8-circle coverage,
exact field freshness). A 7-day window costs about $0.3/month (PUT requests dominate) and gives replay, debugging and
GitHub-vs-live parity checks.

### 7.3 Dependency check: does B break any later step?
| later step | needs | B provides | verdict |
|---|---|---|---|
| Weekly retrain | new training days | GitHub extraction (same scripts) | OK |
| Live serving | last 10 readings, all model fields, static info | `history` written by ingest from the live API; VRS tables (type -> wake, code blocks) packaged with the Lambda or in `models/` | OK (must be built) |
| Feature parity | same X from live and from GitHub | 7-day raw + GitHub day published next day: compare fields and timing for the same aircraft/time | OK, and only possible because of the 7-day archive |
| Live evaluation for 30 days | predictions kept until truth arrives, errors stored | `pending` (6 min) + permanent `metrics/` | OK |
| Dashboard selected aircraft (trail, prediction vs actual) | last 60 min of actuals and predictions | `history` + `pending` | OK; anything older than 60 min is not shown |
| Model rollout and rollback | two model versions, compare live | versioned `models/`, shadow run writes separate predictions, pointer file | OK |
| Frozen live-format test set for the extras model | recorded live data with all fields | copy a 7-day window into `frozen/` before it expires | OK if done in time (calendar item) |
| Debugging a bad prediction | the raw record of that minute | raw archive for 7 days | OK within 7 days |
| If GitHub stops publishing or changes format | own data | only 7 days | RISK: accepted; mitigation = switch to Option C early, and keep extracted Parquet |
| If later we want to retrain on live-format data | weeks of raw | not available | RISK: accepted; decision D16 says we retrain from GitHub |
| adsb.lol API unavailable | live input | none | RISK for any option; alarm on data age, show "stale" |
| Cost | small | about $0.2-0.4/month for archive + history + metrics *(est.)* | OK |
Conclusion: B holds for every planned step. Two accepted risks (GitHub dependency, no long live-format archive).
If either becomes unacceptable, switching to a longer raw retention is a one-line lifecycle change, but data not saved
before the switch cannot be recovered in live format.

---------------------------------------------------------------------------------------------------

## 8. Turning plan (how turns are handled, step by step)

Facts: most aircraft fly straight (level-cruise physics error median about 1.4 km at +5 min; p90 about 17 km, the tail is
turns and level changes). Turns are where a model must beat physics.
1. Identify turns (labels for sampling and reporting only, never a separate input path): regime 1 if the track changed
   by more than 8 degrees in the last 3 minutes, 2 if vertical rate above 2.5 m/s, else 0; and "hard" if the
   straight-line error at +5 min is above 3 km. Done in the builder.
2. Data balance: train windows are 40% uniform, 40% hard, 20% turning/vertical; val/test stay uniform.
3. Fair baselines: straight line AND constant turn rate (turn rate from the last 60 s of track change).
4. Inputs that see a turn early although spaced 60 s apart: roll, track_rate (fixed freshness), heading minus track,
   autopilot heading and altitude deltas, per-step changes dtrack/dgs/dalt, `dt_min`, wake class, position and hour.
5. Targets: position, heading and speed at each of the 5 minute marks (both data sources) plus dense 10 s positions
   (auxiliary, GitHub only). The dashboard draws a cubic Hermite spline through the predicted positions and headings,
   so a turn is a curve, not five joined points.
6. Loss and evaluation: predict the residual over the best baseline; Huber loss; weights so hard windows count more;
   metrics reported separately for level / turning / climb-descent, for seen and unseen aircraft, and for test days.
7. Acceptance for turns: better than BOTH baselines in the turning stratum on val and test; not worse in level cruise.
8. Uncertainty: the dashboard shows a cone; width from the model's error in that regime (quantile head or regime-wise
   error tables).
9. If turns are still weak after step 6: (a) heads specialised by regime (mixture of experts), (b) route features
   (destination bearing/distance from VRS routes, marked as scheduled), (c) "focus mode": poll only the selected
   aircraft every 5-10 s through a cached proxy (rate limits and terms must be checked) and use a second model trained on
   10 s history (data already extracted). Not in the first release.
Time: steps 1-4 already in the builder; steps 5-7 are the first Colab run (about 10-15 min per run, several runs);
step 9 is a separate iteration after the first live results.

---------------------------------------------------------------------------------------------------

## 9. Roadmap with gates

| # | Step | Output | Gate before the next step |
|---|---|---|---|
| 1 | Fix extractor (schema 4) | code | DONE (2026-09-26) |
| 2 | Builder smoke test on current data (logic only) | `data/gru_v2_smoke` | no Python error; test-day straight error about 1.4 km; regime table plausible |
| 3 | Re-extract Round 1 into `data/globe_extract_v4` (about 65 min) | 11 days schema 4 | summary all OK; re-run `validate_extras` with `EXTRACT_DIR=data/globe_extract_v4`: track_rate corr clearly above 0.53 |
| 4 | Build the real dataset from v4 | `data/gru_v2` | summary table: 15% turning/vertical share plausible, unseen share about 20% on test |
| 5 | Baselines table on test | numbers | straight and constant-turn-rate per regime |
| 6 | Upload dataset to Drive, Colab GRU (with masks), ablation A vs B | model + metrics | beats best baseline on hard cases; else iterate (features, loss, more days from Round 2-4) |
| 7 | Round 2-4 downloads in the background (about 39 days, 3-4 h) | more days | learning curve says whether needed |
| 8 | Shared feature module + ONNX export + parity test (ONNX vs PyTorch) | `ml/features.py`, `model.onnx` | identical outputs within tolerance |
| 9 | Live capture upgrade (Option B) + measure 429 dropout + contact adsb.lol | new Lambda, history, raw 7 d | 24 h of stable runs, alarms on data age |
| 10 | Predict Lambda + pending + metrics; replay test on recorded live data; GitHub-vs-live parity check | live predictions | live errors close to offline test errors |
| 11 | CDN + detail API | static JSON, cached detail | p95 load times, no Lambda on the hot path |
| 12 | New map + focus UX | dashboard | 60 fps with ~2,500 aircraft, mobile width OK |
| 13 | Freeze a live test set, shadow-compare, switch model pointer | production model | live metrics >= offline |
| 14 | 30-day live evaluation, weekly retraining, monitoring (data quality, drift), README/CV/portfolio | proof | |
Order rule: step 9 can start as soon as the owner agrees; it does not depend on the model, but step 10 does depend on 8.

---------------------------------------------------------------------------------------------------

## 10. Open items and unverified facts
- Real live 429 dropout rate (run `check_data_quality.py`, v3 "SUDDEN drops").
- Training box margin around the serving area (about 3 degrees) - not applied yet.
- `staging-0` vs `prod-0` identical? Not proven; `prod-0` is used.
- adsb.lol `/api/0/routeset` quality (VRS routes are the plan).
- Firehose vs direct S3 numbers, Lambda durations/memory, CloudFront free tier, tile hosting: estimates only.
- Hugging Face / Kaggle ADS-B datasets were not inspected.
- The 09-19 and 08-21 days of our own bronze are partial (Lambda/AWS outage days).
- Whether all Round 2-4 dates exist on GitHub (the extractor reports missing days; not surveyed for 2025 dates except
  2025-10-15 and 2026-01-15).

---------------------------------------------------------------------------------------------------

## 11. Log of new findings (append-only)

### 2026-09-26 02:15 builder smoke test (`build_windows_v2.py --smoke`, exit 0, 11 s; run_logs/2026-09-26_021507_windows_smoke.log)
- Runs without error on schema-3 data (train 09-15 and test 09-23, 20k windows each), X (n,10,31), Y (n,30,2).
- Test uniform windows: straight-line error @+5 min median 1.12 km, p90 13.7, p99 41.3 (all-rows number in 3.4 was 1.43 / 16.95:
  windows need a full 10-reading history, so brand-new and short tracks are excluded, and windows keep only rows with valid speed/track/alt).
- Regimes on test: level 63.4% (straight median 0.72 km), turning 16.5% (5.54 km, p90 29.5), climb/descent 20.0% (4.53 km).
  So turning windows have 8x the straight-line error of level windows: this is where a model can win.
- Constant-turn-rate baseline (turn rate of the last 60 s held for 5 min) is WORSE than straight (median 1.46 vs 1.12; turning 12.7 vs 5.5 km).
  Code checked (build_windows_v2.py lines 261-266): sign and time step are right. Reason: most turns end within about a minute, so
  continuing them 5 minutes draws a circle. Consequence: it stays only as a weak reference; the real target to beat in turns is the
  straight line, and the model must learn HOW LONG a turn lasts (roll, autopilot heading, track_rate help).
- Unseen-aircraft share on the test day 14.1% (aim ~20%: holdout is by hex folder, but only aircraft in the first-1 GB sample count).
- Train mix 40/40/20 comes out as kinds {0: 7998, 1: 7990, 2: 3999}; hard>3km share among candidates 34.6%, turning/vertical 36.7%.

### 2026-09-26 pre-download review of the extractor (owner: "yeh download firse nahi karunga... abhi check karlo jo chiaye lele")
Goal: the Round 1-4 download must be done ONCE; nothing we may need later may be thrown away. Full read of extract_globe_day.py; changes (SCHEMA 5):
- Box margin: extract lat 32-67, lon -16..36 (3 degrees around the serving area lat 35-64, lon -13..33), so windows near the edge keep
  their 5-minute future. Before, aircraft leaving the box were never trained on (edge/survivorship bias). Builder `candidates()` now
  only takes windows whose LAST history point is inside the serving area. (Output size rises, est. +30-40%, unverified.)
- Freshness columns `obj_age_s` (seconds since the last aircraft-object sample) and `trate_age_s` (distance to the nearest track_rate
  sample): they tell the model/mask how old a held/interpolated value is, and let validation look at fresh rows only. Unit-tested on
  a synthetic trace (values correct).
- OBJ_INTERP_GAP_S 45 -> 60 s: track_rate is present in only 27% of objects and objects on 25% of points, i.e. roughly one track_rate
  sample per minute; a 45 s limit would have thrown away too much.
- Known and accepted (documented, not stored): MLAT/TIS-B points (about 2.3% live) are dropped; object keys not stored: type (source is
  in point[9]), sil_type, version, nic_baro, alert, spi; heatmap/ and acas/ folders; raw irregular timestamps (the 10 s grid keeps
  the motion; gap_s keeps how far apart the real points were). Linear interpolation of lat/lon between real points is fine because
  readsb writes more points during turns.
- What is stored per row: see 6.1 (41 columns) + obj_age_s + trate_age_s = 43.
- Gate before the full 65-minute run: one 60 MB smoke day into data/globe_extract_smoke5, then validate_extras on it (track_rate corr
  should rise clearly above 0.53; fresh rows trate_age_s<=10 should be higher still).

### 2026-09-26 02:26 smoke5 (schema 5, 60 MB of 2026-09-15, exit 0, 160 s; run_logs/2026-09-26_022326_smoke5.log)
- Works: 1,558 files, 245 Europe aircraft, 373,975 rows, output 22 MB, 5 hex folders in 60 MB; speed 0.3 MB/s at the start (GitHub slow now).
- FINDING (hex folders): the tar holds ~85-97 of the 256 hex folders per day (the first 1 GB), NOT all 256. Different days hold different
  subsets (overlap of consecutive days 0-81 of ~88); the union over the 11 Round 1 days is all 256. So every day is a random third of
  the aircraft by ICAO suffix, unbiased. Consequence for the aircraft-level holdout (20% of hex folders): it still works, because the
  builder removes those folders from training days; expect the "unseen" share on a test day to be ~14-20% (we saw 14.1%), not exactly 20%.
- Validation of track_rate on this file: see the next entry.

### 2026-09-26 02:28 validate_extras on smoke5 (schema 5; run_logs/2026-09-26_022813_validate_smoke5.log) - GATE PASSED
- track_rate vs real turn: corr 0.533 -> 0.772, slope 0.295 -> 0.731, sign agreement 81% -> 95.2% (fresh samples only, trate_age_s<=10 s: 0.786 / 0.756 / 95.9%).
  Ceiling is below 1 because an instantaneous rate is compared with the change over the NEXT 10 s. Same day-sized sample: 11.6k pairs, 245 aircraft,
  one day, so treat the exact numbers as approximate; the improvement is large and consistent (roll vs track_rate physics check also rose 0.79 -> 0.90).
- All other checks unchanged: wind FROM (0.909/0.914), mach/tas/oat ratio 1.000, ias/tas 0.93 low / 0.57 cruise, nav_alt 92.9%, vrate 0.968, nav_heading 64.7% (weak, mask).
- Decision: run the full Round 1 with schema 5 into data/globe_extract_v5 (old data/globe_extract kept until the new one is verified).

### 2026-09-26 ~02:40 Round 1 v5 download started (run `extract_r1_v5`, data/globe_extract_v5) + training script written
- Started by the owner; cron job checks CURRENT.log every 2 minutes. First day: 300 MB read at 2.5 MB/s, 797 Europe aircraft (with the 3-degree margin).
- `ml/scratch/train_gru_v2.py` (compiled, NOT yet run): loads data/gru_v2/<split>/<day>/{X,S,Y,YH,YV,meta}; variant A = 12 core features,
  variant B = core + 10 extras + 9 masks (missing extras = 0 after normalisation, normalisation stats use only reported values);
  model = Linear+GELU -> 2-layer GRU(128) over the 10 steps + embeddings (category, wake) + numeric statics -> MLP(256) -> residual over the
  straight-line baseline for the 30 positions (unit 10 km), heading (sin,cos) and speed change at the 5 whole minutes; loss = Huber on all 30
  positions (weight x2 at minute marks, x4 at +5 min) + 0.2 cosine heading + 0.1 Huber speed; AdamW + OneCycle, batch 1024, best-val checkpoint.
  Report: median/mean/p90/p99 at +5 min for model vs straight line, for all / level / turning / climb-desc / unseen aircraft, win rate, and median by
  minute 1..5; `--train-frac` for the learning curve; smoke datasets without a val split use the last 10% of train.
  All hyper-parameters are first guesses. Acceptance gate (unchanged): beat the straight line in the turning stratum on val AND test, and not be worse
  in level cruise. Baseline note: "constant turn rate" is not used as the reference in training because it is worse than straight (see 09-26 02:15 entry).
- Not yet done in the script: ONNX export, quantile/uncertainty head, dt_min/jitter augmentation, use of trate_age_s as a mask.

### 2026-09-26 ~02:55 prepared while Round 1 v5 downloads (day 2 of 11 running, 6 MB/s)
- `ml/colab/train_gru_v2.ipynb`: mounts Drive, copies data/gru_v2 to the local Colab disk, trains B, then A (ablation), then the learning curve
  (25% / 50% of the train windows), prints test metrics, copies runs back to Drive. Needs on Drive: `MyDrive/liveflights/gru_v2/` and `train_gru_v2.py`.
- `ml/scratch/export_onnx.py` (not yet run; needs `pip install onnx onnxruntime`): raw X,S in -> pos/hdg/spd out (normalisation + baseline inside the
  graph), then compares ONNX with PyTorch on 2000 test windows.
- `ml/features.py` = SHARED FEATURE MODULE (decision D14): `window_x()` and `window_s()`, `FEATURES_VERSION = 1`. `build_windows_v2.py` now calls these (its own
  copy of the maths was removed). The live predict Lambda must import the same file. Refactor check still to do: rebuild the smoke set and compare with the
  saved pre-refactor arrays (copy in the session scratchpad: gru_v2_smoke_before); they must be identical (same RNG seed).
- Data size estimate for Drive (unverified): gru_v2 about 3 GB (train 1.2M windows x 10 x 31 float32 = 1.5 GB for X alone).

### 2026-09-26 03:00 Round 1 v5 DONE (run extract_r1_v5, exit 0, 29.9 min, data/globe_extract_v5, schema 5, all 11 days OK)
| date | aircraft | rows | out MB |
|---|---|---|---|
| 09-01 | 4,208 | 6,436,221 | 355 |
| 09-03 | 4,161 | 6,456,035 | 357 |
| 09-05 | 4,412 | 6,857,541 | 377 |
| 09-07 | 4,372 | 6,685,814 | 367 |
| 09-09 | 4,011 | 6,311,683 | 357 |
| 09-11 | 4,074 | 6,065,224 | 337 |
| 09-13 | 4,303 | 6,661,337 | 370 |
| 09-15 | 4,176 | 6,239,410 | 342 |
| 09-17 (val) | 4,010 | 6,160,385 | 344 |
| 09-20 (test) | 4,399 | 6,772,279 | 376 |
| 09-23 (test) | 4,220 | 6,223,692 | 342 |
Total 71.3M rows (Round 1 v3 had 69.3M: the 3-degree margin added only ~3% rows and ~15% size, less than the 30-40% guessed). GitHub speed rose from 3 to 12 MB/s
during the run (about 3 min per day instead of 4-6). Next: validate_extras on v5, then rebuild the smoke set to check the shared-features refactor, then the full dataset.

### 2026-09-26 03:58 validate_extras on Round 1 v5 (09-05 and 09-13, 13.5M rows; run_logs/2026-09-26_035752_validate_v5.log) - GATE PASSED on full data
- track_rate vs real turn: corr 0.801 (was 0.533 on v3), slope 0.687 (was 0.295), sign agreement 94.5% (was 81%); fresh samples only (<=10 s): 0.832 / 0.739 / 95.9%.
  Pairs used fell from 441k to 399k: with interpolation limited to 60 s, some rows now have no track_rate (fewer but correct values).
- roll vs track_rate physics 0.931 (was 0.793). Wind FROM 0.927/0.891, mach/tas/oat 1.000, ias/tas 0.933/0.571, nav_alt 93.5%: all unchanged and fine.
- Decision: data/globe_extract_v5 is the training source. The old data/globe_extract (schema 3, 3.3 GB) is kept for now as the reference for the refactor check only.

### 2026-09-26 04:00 shared-features refactor VERIFIED
- First rebuild (windows_smoke2) crashed: my refactor had removed the variable `trk` still used by the baseline code (`NameError`, exit 1). Fixed (`np.radians(trk_deg[:, -1])`);
  scanned all four new scripts for undefined names with an AST check (none). Lesson: a comparison against old files is meaningless when the run failed; check exit code first.
- windows_smoke3 (exit 0, 9 s): X, S, Y, YH, YV of train (09-15) and test (09-23) are bit-identical (np.array_equal) to the pre-refactor arrays, meta equal, and the
  regime/baseline table is unchanged. So `ml/features.py` reproduces the old features exactly.

### 2026-09-26 04:01 train_smoke (train_gru_v2.py on the 20k-window smoke set, 2 epochs, mps, exit 0, 9 s) - found and fixed a bug
- Training runs and learns: on val (a slice of the train day, hard-case mix) the model already beats the straight line after 2 epochs (median +5 min 3.87 vs 4.68 km, wins 64%; turning 4.52 vs 5.29).
- BUG: on the test day the model error was about 1,291 km (garbage). Cause (verified with a one-off check): the smoke train set is ONE day, so the day-of-week
  features have std 0.00015; the test day (another weekday) then normalised to values up to 6,700. Fix: static-feature std below 0.05 is replaced by 1, and the numeric
  static features are clamped to +-10 (X was already clamped). With 8 train days spanning all weekdays this cannot happen, but the model must be robust anyway
  (a live day is always "new"). Also silenced a float(requires_grad) warning.
- Val vs test numbers on a 1-day smoke set say nothing about model quality; only the full dataset does.

### 2026-09-26 04:03 train_smoke2 (after the fix; exit 0, 5 s)
- Test day now sane: model median +5 min 1.78 km vs straight 1.12; turning 4.63 vs 5.54 (model wins 63%), climb/descent 3.78 vs 4.53 (wins 66%), but LEVEL cruise
  1.41 vs 0.72 km (model wins only 30%). After only 2 epochs on 18k windows of ONE day this is expected: the train mix is 40% hard + 20% turning/vertical (about 3x more hard
  cases than reality), so the untrained model "corrects" straight flights too. This is exactly the acceptance gate ("beat straight in turning AND not worse in level"):
  the real run must close the level gap. If it does not: (a) raise the uniform share of the train mix (40/40/20 -> 60/25/15), (b) weight the loss by the inverse sampling
  probability of each kind, (c) initialise the residual head at zero and add weight decay on it, (d) more epochs/data (the smoke run saw 1 day and 2 epochs).

### 2026-09-26 09:39 FULL DATASET BUILT (run build_full, exit 0, 60 s - my 5-15 min estimate was far too pessimistic) -> data/gru_v2, 2.88 GB
| split | windows | aircraft | note |
|---|---|---|---|
| train (8 days 09-01..09-15) | 1,199,198 (479,792 uniform; kinds 0/1/2 = 479,792 / 479,602 / 239,804) | 11,570 | unseen share 0% (holdout folders excluded) |
| val (09-17) | 99,971 | 2,732 | uniform |
| test (09-20, 09-23) | 499,808 | 7,363 | uniform, unseen-aircraft share 18.5% |
Baselines on the test set (uniform windows, +5 min): straight line median 1.14 km, p90 13.33, p99 41.9; constant-turn-rate median 1.45 (worse, see 02:15 entry).
Test by regime, straight-line median / p90: level 63.6% of windows 0.73 / 6.00; turning 16.1% 5.09 / 29.59; climb/descent 20.4% 4.52 / 19.75.
Val: 1.23 / 14.55; train uniform slice: 1.16 / 13.81 (consistent across splits, so no split is unusual). Windows dropped for NaN: 29-148 per day (0.1%).
Caveat kept in mind: 81.5% of test windows come from aircraft that also appear in the train days (regular routes); the `unseen` stratum (18.5%) is the generalisation test.
Training script: added `--stream` (data stays in RAM, only batches go to the GPU; for the 8 GB Mac) and `--test-max N` (random test subset for quick local runs).

### 2026-09-26 17:01 FIRST REAL TRAINING RUN: variant B, local Mac (mps), 25% of train (299,799 windows), 8 epochs, 129 s, exit 0 (run train_local_B; runs/local_B/)
Test = 100,000 random windows of the 2 test days (uniform, real distribution). Error at +5 min in km, model vs straight line:
| stratum | n | model median / mean / p90 / p99 | straight median / mean / p90 / p99 | model wins |
|---|---|---|---|---|
| all | 100,000 | 1.02 / 3.48 / 9.96 / 28.9 | 1.15 / 4.73 / 13.45 / 42.2 | 64% |
| level | 63,273 | 0.65 / 2.03 / 5.45 / 20.3 | 0.73 / 2.28 / 6.02 / 23.5 | 60% |
| turning | 16,147 | 3.45 / 6.54 / 17.05 / 36.3 | 5.17 / 10.35 / 29.91 / 53.3 | 68% |
| climb/descent | 20,580 | 2.65 / 5.57 / 14.98 / 33.3 | 4.49 / 7.86 / 19.81 / 45.0 | 74% |
| unseen aircraft | 18,693 | 1.05 / 3.58 / 10.19 / 29.2 | 1.21 / 4.83 / 13.79 / 42.6 | 64% |
Median error by minute (+1..+5): model 0.07 / 0.19 / 0.37 / 0.64 / 1.02, straight 0.07 / 0.20 / 0.42 / 0.73 / 1.15. Val (uniform, 99,971): mean 3.72 vs 5.10, median 1.09 vs 1.23 (same picture, no overfit to val).
- ACCEPTANCE GATE: PASSED (turning much better than straight AND level not worse). The level-cruise worry from the smoke run is gone with more data/epochs.
- Reductions vs straight line: mean -26% overall, turning mean -37% (p90 -43%), climb/descent mean -29% (median -41%). Unseen aircraft behave like seen ones (generalises).
- Learning: val mean 4.13 -> 3.72 km over 8 epochs, flattening at epochs 6-8 (3.738 / 3.719 / 3.716): this size of data/model is nearly saturated; the full 1.2M windows, more epochs or a larger net are the next levers.
- NOT yet known: how much of the gain comes from the extra fields (needs variant A, same settings). NOT yet tested: 60-s-jitter robustness, tail errors (p99 still 29 km), the 30-day live behaviour.

### 2026-09-26 17:05 ABLATION A vs B (same settings: 25% train, 8 epochs, same test subset; runs/local_A vs runs/local_B; A = 12 core features, B = core + 10 extras + 9 masks)
Test, +5 min error (km), A -> B:
| stratum | A mean / median / p90 / p99 | B mean / median / p90 / p99 | B vs A (mean) |
|---|---|---|---|
| all | 3.69 / 1.03 / 10.68 / 30.50 | 3.48 / 1.02 / 9.96 / 28.86 | -5.7% |
| level | 2.13 / 0.64 / 5.78 / 21.98 | 2.03 / 0.65 / 5.45 / 20.29 | -4.7% |
| turning | 6.99 / 3.72 / 18.29 / 37.20 | 6.54 / 3.45 / 17.05 / 36.25 | -6.4% |
| climb/descent | 5.88 / 2.75 / 16.05 / 35.50 | 5.57 / 2.65 / 14.98 / 33.30 | -5.3% |
| unseen aircraft | 3.77 / 1.06 / 10.97 / 30.69 | 3.58 / 1.05 / 10.19 / 29.16 | -5.0% |
Straight line for reference: mean 4.73 overall, 10.35 turning. So model A already cuts the straight-line mean by 22% (turning -32%) using only the 12 core fields (position, speed, track, vertical
rate, altitude and their per-step changes); the extras add a further ~5-6% in every stratum (turning: -37% vs straight instead of -32%). Val agrees (mean 3.72 B vs 3.92 A).
- Honest reading: the extras help consistently (val, test, every stratum, unseen aircraft) but the gain is modest and each variant was trained ONCE (one seed): a difference of 0.2 km
  is probably real because it is consistent, but it is not proven. The larger part of the improvement over physics comes from the core history itself.
- Consequence for the live system: the core-field model works on the data our current pipeline already has; the extras need the upgraded ingest (all fields), justified by ~5-6% and by turning p90.
- Possible next levers (unmeasured): full 1.2M windows (A/B both saturated at 25%), bigger network (`--hidden 256`), regime-aware loss, multi-seed check.

### 2026-09-26 20:27 FULL-DATA RUN: variant B, all 1,199,198 train windows, 12 epochs, mps --stream, 841 s (14 min), exit 0 (run train_full_B; runs/full_B/model.pt = model candidate v1)
Test (same 100,000-window subset as before), error at +5 min in km; model vs straight line; and vs the 25%-data run:
| stratum | full B mean / median / p90 / p99 | straight mean | vs straight (mean) | 25% B mean |
|---|---|---|---|---|
| all | 3.29 / 0.97 / 9.36 / 27.26 | 4.73 | -30% | 3.48 |
| level | 1.97 / 0.63 / 5.28 / 19.67 | 2.28 | -14% | 2.03 |
| turning | 5.96 / 3.15 / 15.46 / 34.09 | 10.35 | -42% (p90 -48%) | 6.54 |
| climb/descent | 5.26 / 2.56 / 13.86 / 31.83 | 7.86 | -33% | 5.57 |
| unseen aircraft | 3.39 / 1.02 / 9.58 / 27.74 | 4.83 | -30% | 3.58 |
Model wins 66% of all windows (70% turning, 75% climb/descent). Median by minute (+1..+5): model 0.05 / 0.17 / 0.35 / 0.62 / 0.97 vs straight 0.07 / 0.20 / 0.42 / 0.73 / 1.15.
Val (99,971): mean 3.50 vs 5.10, median 1.05 vs 1.23 (matches test, no overfit).
- Learning curve point (B): 25% of the data -> 100% of the data (4x) improved the test mean 3.48 -> 3.29 km (-5.5%), turning 6.54 -> 5.96 (-9%). Val mean was still creeping down at epoch 12 (3.504 / 3.501 / 3.500),
  i.e. converged for this network size. Diminishing returns: 4x more windows gave -5.5%, so Rounds 2-4 (more days) will probably add a few % at most. The model size (358k parameters) or better loss/features
  are the other levers. Epoch time about 70 s on the Mac (mps, stream); the whole run fits in 14 minutes, no Colab needed so far.
- p99 error is still 27 km (rare hard cases: sharp/late turns, holding patterns, go-arounds), the uncertainty cone on the dashboard has to show this.
- Model v1 candidate: runs/full_B/{model.pt, norm.json, metrics.json}. Not yet exported to ONNX; not yet tested with 60-s jitter/dropouts; not yet compared on our own serving-like S3 data (core-field model A would be needed for that).

### 2026-09-26 21:55 ONNX export of model v1 (run export_onnx_v1, exit 0, 4 s) - runs/full_B/model.onnx (1.47 MB)
- Parity ONNX vs PyTorch (2000 test windows): max difference pos 0.000023 km (2 cm), heading 0.000011, speed 0.000031 m/s.
- Extra check (one-off script) at other batch sizes, because torch warned that GRU + variable batch can break: batch 1, 2, 7, 64, 2500, 4000 all fine, max diff <= 0.000023 km.
- Speed (onnxruntime CPU on the Mac, one call): 1 window 1 ms, 2,500 windows 86 ms, 4,000 windows 146 ms. A live minute has about 2,500 aircraft, so one predict call is ~0.1 s here; a 512 MB Lambda
  will be slower than an M1 but far inside a 60 s budget (unverified on Lambda).
- Export uses the classic (TorchScript) exporter with dynamo=False; it prints deprecation warnings only. The graph contains normalisation, the straight-line baseline and the residual, so the Lambda feeds raw X (n,10,31) and S (n,10).

### 2026-09-28 (while train_full_B_h256 runs) destination-route feature written, not yet tested
Prepared code for the #2 lever from the "make the model better" list (destination bearing/distance from the scheduled route), as a NEW schema so the running
h256 training (reading data/gru_v2, S width 10) is untouched:
- `ml/features.py` FEATURES_VERSION 1 -> 2: added `bearing_distance()` (haversine distance km + initial bearing) and `window_s_dest()` (old window_s() output + 4 columns:
  dest_dist_norm = log1p(km)/log1p(2000), dest_sin, dest_cos of the bearing, m_dest mask). S grows from 10 to 14 columns only when this path is used.
- `ml/scratch/build_windows_v2.py`: `load_dest_table()` joins VRS `routes.csv` (callsign -> AirportCodes, last code = destination) with `airports.csv` (ICAO -> lat/lon); on
  2026-09-23 this matched 54.3% of the 99.6% of aircraft that report a callsign (checked in a one-off script). `load_day()` now also selects the `callsign` column.
  New `--dest` flag: when given, builds S with window_s_dest() using the callsign at the window's last history row, default output data/gru_v2_dest; without it, output and
  S are byte-for-byte the same as before (verified by re-reading the code path: the `dest is None` branch is untouched). meta gets an `m_dest` column; summarize() prints
  the destination-known share per split.
- `ml/scratch/train_gru_v2.py`: `Norm` detects `use_dest` from `S.shape[-1] == 14`; when true it normalises dest_dist_norm using only the rows with a known destination (like
  the X extras) and adds 4 columns (dist, sin*mask, cos*mask, mask) to the static input; `GRUNet` takes a new `s_num_dim` argument (8 normally, 12 with dest) sizing the head's
  input layer; `norm.json` records `use_dest`/`s_num_dim`/`dest_mean`/`dest_std`. Existing runs/full_B and the in-progress runs/full_B_h256 are S-width-10 and completely
  unaffected (their code path is the untouched `else` branch everywhere). `export_onnx.py`'s `load_norm()` updated to restore the new fields.
- NOT YET RUN: no `--dest` build, no training on it. Plan once h256 finishes: `--smoke --dest --extract-dir data/globe_extract_v5` (quick logic check, ~10s), then the full
  `--dest --extract-dir data/globe_extract_v5 --out data/gru_v2_dest` build (~1 min), then a variant-B training run on it, compared against runs/full_B (no dest) at the
  same epoch count. Expectation from the plan discussed with the owner: distance/bearing to the scheduled destination should help most in the turning/climb-descent strata
  (an aircraft usually turns towards where it is actually going), more than the ~5-6% the other extras gave; not yet measured.

### 2026-09-28 03:04 EXPERIMENT #1 RESULT: bigger model (hidden 256, 1,019,431 params vs 358,439), full data, 12 epochs, 1573 s (26 min), exit 0 (run train_full_B_h256; runs/full_B_h256/)
Test, +5 min error (km), h256 vs h128 (runs/full_B, previous entry):
| stratum | h128 mean | h256 mean | h128 turning mean | h256 turning mean |
|---|---|---|---|---|
| all | 3.29 | 3.30 | 5.96 | 5.97 |
| level | 1.97 | 1.98 | - | - |
| climb/desc | 5.26 | 5.28 | - | - |
Every number matches within about 0.01-0.02 km (noise-level), across all epochs 1-12 the two runs tracked each other almost exactly (e.g. epoch 9 mean 3.514 vs 3.514, epoch 10 3.504 vs 3.508).
- RESULT: bigger model did NOT help. Confirms the "model size" lever from the 09-26 list is NOT the bottleneck; the plateau at epoch ~10 is a genuine data/feature/loss limit, not
  a capacity limit. Model size is closed as a lever for now (no need to try hidden 512 etc.).
- Consequence: the next real lever is #2, destination-route features (already coded, see the entry above), or #5 (loss/sampling changes) or #6 (more days). Decision: try
  destination next, since the theory (aircraft turns towards where it is actually going) is a genuinely different signal, not just "more of the same".

### 2026-09-28 16:45 destination dataset built (run build_dest, exit 0, 68 s) -> data/gru_v2_dest, 2.91 GB, S width 14
- Fixed my own bug from the smoke test: `--smoke --dest` had both mapped to the same output path `data/gru_v2_smoke`, so the dest smoke run silently overwrote the older
  plain smoke dataset (S width 10 -> 14) there. No real training data or saved model touched (data/gru_v2, runs/full_B, runs/full_B_h256 untouched). Fixed the path logic
  (`data/gru_v2` + `_smoke`/`_dest` suffixes combine correctly now); noted to the owner.
- Window counts per day are IDENTICAL to the non-dest build (same candidate selection, same RNG seeds; dest only adds S columns) - confirms the dest code path does not
  change which windows get picked, only what's in S. Destination-known coverage: train 81.4%, val 83.0%, test 82.7% (higher than the 54.3% estimated from a single day's
  aircraft-level callsign match; the per-window callsign, held up to 30 min, apparently matches more often than the "first callsign of the day" check did).
- Next: train variant B on data/gru_v2_dest (same settings as runs/full_B: hidden 128, 12 epochs, full data) and compare directly against runs/full_B (no dest).

### 2026-09-28 17:05 EXPERIMENT #2 RESULT: destination-route feature, full data, 12 epochs, 742 s (run train_full_B_dest; runs/full_B_dest/) - MIXED, not a clean win
Test, +5 min error (km), no-dest (runs/full_B) vs with-dest (runs/full_B_dest):
| stratum | no-dest mean / median / p90 | with-dest mean / median / p90 | mean change | median change | win rate |
|---|---|---|---|---|---|
| all | 3.29 / 0.97 / 9.36 | 3.04 / 1.21 / 8.00 | **-7.6%** | **+25%** | 66% -> 60% |
| level | 1.97 / 0.63 / 5.28 | 2.04 / 0.83 / 5.11 | +3.6% | **+32%** | 62% -> 52% |
| turning | 5.96 / 3.15 / 15.46 | 5.11 / 2.77 / 12.77 | **-14.3%** | **-12%** | 70% -> 70% |
| climb/desc | 5.26 / 2.56 / 13.86 | 4.52 / 2.55 / 10.88 | **-14.1%** | 0% | 75% -> 74% |
| unseen aircraft | 3.39 / 1.02 / 9.58 | 3.14 / 1.24 / 8.14 | -7.4% | +22% | 66% -> 60% |
- GOOD: turning and climb/descent both improved a lot more than the core-extras gain did (turning mean -14.3% vs the +5-6% the roll/wind/etc. extras gave). p90/p99 fell in every
  stratum (fewer big misses; e.g. test p90 all 9.96 -> 9.42 [h128] -> 8.00 [dest]). This is exactly the "aircraft turns towards where it's going" effect we hoped for.
- BAD: level-cruise MEDIAN got 32% worse (0.63 -> 0.83 km) and the model's win rate on level windows fell to 52% (barely better than straight line, was 60-62% before).
  Overall median also got worse (+25%) even though mean got better -7.6%: destination info is making the common, easy, straight-flying case slightly noisier while fixing
  the rare/hard cases (mean is pulled by large errors, median is the typical case). Consistent across val AND test (not a fluke of one split).
- Likely cause (not proven): destination bearing is a straight-line "where it eventually goes" signal that does not match the current instantaneous track during ordinary
  level cruise (real routes have waypoints, are rarely a straight line to destination), so the network partly "listens" to it even when the flight is currently level and
  the straight-line guess was already almost perfect; only 12 epochs on this new input, so it may not have fully learned to down-weight destination when |dtrack|~0.
- DECISION NEEDED (not yet made): this is not an unambiguous win, unlike core extras or the shared-features refactor. Two options for the next experiment: (a) more epochs /
  a regime-aware loss so the network learns to trust destination only when the current track deviates from the direct-to-destination bearing, or (b) mask dest to 0 whenever
  the last 3 turn-lookback steps show <2 deg track change (roughly regime==0) so the level case never sees it. Neither tried yet. Model v1 (runs/full_B, no dest) remains the
  safer default until this is resolved.

### 2026-09-28 owner review of decisions: destination, architecture, data retention scope, live testing
- Destination feature: owner wants it kept ("bhot important hai"), not dropped despite the level-cruise regression. Decision: keep working on it, next step is to fix the
  level-cruise regression (mask dest to 0, or down-weight it, when the last 3 steps show <2 deg track change) rather than either shipping it as-is or abandoning it.
- Data-retention scope corrected by the owner: Option B (7-day raw archive) was reasoned about only for trajectory prediction; other planned models (corridor/route
  mining, traffic forecasting, anomaly detection) might want more live-format history. Owner's own conclusion, which I agree with: "hamara sara kaam aircraft ko live
  stream karke show karne ka hai; models train toh ham locally GitHub data se bhi kar lenge" - i.e. the live pipeline's job is serving/display/evaluation, not being the
  training archive; any future model (corridor, traffic, anomaly) trains from GitHub exactly like the trajectory model does, since GitHub already covers our whole
  Europe area comprehensively. REFINEMENT to Option B: add a cheap PERMANENT (kept forever, tiny) layer of aggregated statistics - hourly/daily aircraft counts per
  region, a density/heatmap snapshot, permanent prediction-accuracy metrics - separate from the 7-day raw archive. This gives corridor/traffic/anomaly dashboards a long
  time series without hoarding raw per-aircraft records. Nothing raw needs to live longer than 7 days. Not yet implemented; to design when the live ingest Lambda is built.
- Live testing now: variant A (core-only, 12 features: position/speed/track/vrate/alt and their per-step changes) needs ONLY the 16 fields our live Lambda already
  captures, so it COULD be tested against the real live feed today, with no AWS/pipeline change. Variant B (extras) cannot, since roll/track_rate/wind/nav_heading are
  not currently captured live. No live-serving code exists yet at all (no rolling per-aircraft history, no predict step, no prediction-vs-actual comparison) - this is
  all still to be built. Proposed next step: a small LOCAL script (no AWS change) that polls live/latest.json (or the public API directly) once a minute for ~15-20 min,
  builds a 10-reading history per aircraft with ml/features.py, runs the exported ONNX model (variant A), stores the prediction, waits 5 min, compares to the actual
  position. This is the fastest path to a first real "predicted vs actual" proof point for the dashboard pitch, before building the full Lambda infrastructure.

### 2026-09-28 live-serving code written (owner: capture all fields, ship model B live, no A) - NOT DEPLOYED, local only
Owner decision recorded: capture all extra fields in the live Lambda, ship variant B (core+extras) as the only/default model, drop variant A entirely.
Wrote the following, all verified LOCALLY with fake/stubbed AWS clients (no real AWS call made, nothing deployed, nothing costs anything yet):

1. `ingestion/schemas/adsb_lol_extras_mapping.py` (NEW): `map_to_ml_fields(ac, now)` - pure-Python, zero deps (same constraint as the existing
   `adsb_lol_mapping.py`, vendored into the ingest Lambda zip unmodified). Extracts roll/track_rate/true_heading/mach/tas/ias/wind/nav_alt_mcp/nav_heading/category/
   type_code/callsign from a raw adsb.lol `ac` row, in the SAME units/names `ml/features.py` expects. Returns None for rows the training pipeline would also drop
   (on ground, no position, non-ICAO `~` address) - kept SEPARATE from `map_to_flight_state_dict` so the canonical 16-field FlightState contract (CLAUDE.md "Data
   contract") is never touched.
2. `infra/terraform/lambda_ingest/handler.py` (EDITED, additive): `_fetch_one_point` now also builds `_ml_extras` per row via the new mapping; `handler()` pops
   `_ml_extras` off each state BEFORE the canonical Firehose/live-snapshot/hourly-stats writes (verified: those three functions see byte-identical input to before);
   new `_update_history()` writes a compact rolling `live/history.json` (one combined S3 object, read-modify-write, same cost pattern as `_update_hourly_stats` -
   NOT one write per aircraft, which is exactly the ~$155/mo DynamoDB mistake this codebase already paid for once and explicitly avoids). Per aircraft: last
   HISTORY_WINDOW_MINUTES=15 min of readings, skip a reading if <40s since the last kept one (avoids near-duplicates), drop an aircraft entirely if unseen for
   HISTORY_STALE_MINUTES=20 min (bounds the object's size). Reading = compact list `[ts, lat, lon, alt_baro_m, gs_ms, track_deg, vrate_baro_ms, vrate_geom_ms,
   roll_deg, track_rate, true_heading, mach, tas_ms, ias_ms, wd_deg, ws_ms, nav_alt_mcp_m, nav_heading]` (18 numbers, no repeated field names - keeps the object
   smaller than a dict-per-reading would). History update wrapped in try/except like the hourly-stats rollup: additive, never sinks core ingestion.
   VERIFIED (offline, fake S3): non-ICAO/on-ground rows correctly dropped; 3 polls -> 3 readings of the right shape; a reading <40s after the last kept one is
   skipped; readings older than 15 min are trimmed; an aircraft unseen past 20 min is dropped entirely. IAM: no change needed - the existing `LiveStateWrite`
   statement already grants s3:PutObject/GetObject on `live/*`, and `live/history.json` falls under that prefix.
   SIZE ESTIMATE (measured on synthetic data, not real traffic): ~9.6 MB for 4,000 aircraft x 15 readings - this is a real, non-trivial GET+PUT every minute;
   recommend bumping the ingest Lambda's memory from 256 to 512 MB before deploying (NOT changed yet - a cost-affecting infra value, left for the owner to confirm).
3. `predict/handler.py` (NEW): the predict Lambda. Reads `live/history.json`; per aircraft, `_select_window()` walks backwards picking readings ~60s apart
   (tolerating +-15s jitter) until it has HIST=10 or gives up - the exact "last 10, 60s apart" shape the model was trained on, built from whatever irregular
   live polling cadence actually happened (a missed poll, a 429, a cold start); skips an aircraft with too little/too irregular history yet. `_predict_one()`
   calls `ml/features.py`'s `window_x()`/`window_s()` (the SAME module the training pipeline uses - copied into the container image unmodified, not reimplemented,
   so live serving cannot silently drift from what the model was trained on) then runs the ONNX model, writes `live/predictions.json` (current prediction per
   aircraft: a 30-point path plus the +5min point, for the dashboard). `_eval_pending()` compares predictions whose target time has passed against the closest
   actual reading (within 45s); `_update_metrics()` rolls evaluated errors into a PERMANENT tiny `metrics/daily.json` (one row per day: count, mean, p90 via a
   bounded 2000-sample reservoir) - this is what proves "how accurate are we" on the dashboard, and deliberately does NOT expire with the 7-day raw archive.
   Model + norm.json loaded from S3 (`models/trajectory.onnx`, `models/trajectory_norm.json`) once per cold start and cached module-level, same pattern as
   `api/cloud/app.py`'s `forecast_gbr.joblib` loading. Wired for the non-dest model only (S width 10) for now - extending to `window_s_dest` is a small later
   change once the destination feature's level-cruise regression is fixed and it becomes the shipped model.
   VERIFIED END TO END (offline, real trained ONNX model `runs/full_B/model.onnx`, synthetic 20-minute turning flight, no AWS): window selection correctly picks
   10 readings 60s apart out of 20; the model's predicted +5min bearing (142.7 deg) closely matches the synthetic flight's actual current track (150 deg) - a
   real sanity check, not just "it ran without crashing"; `_eval_pending` correctly matches a synthetic "actual" reading placed at the target time and computes
   a plausible error (1.34 km), correctly empties the pending set once evaluated.
4. `predict/Dockerfile` + `predict/requirements.txt` (NEW): container Lambda (numpy + onnxruntime==1.30.0, matching the exporter used), same
   `public.ecr.aws/lambda/python:3.12` base and build pattern as `api/cloud/Dockerfile`; copies `ml/features.py` in unmodified.
5. `infra/terraform/lambda_ingest.tf` (EDITED): added the new extras-mapping module to the ingest zip's vendored sources.

NOT DONE (deliberately, needs the owner's go before any AWS/cost-touching action):
- No terraform written yet for the predict Lambda itself: no ECR repo, no `aws_lambda_function`, no IAM role/policy (needs GetObject on live/*+models/*, PutObject
  on live/*+metrics/*, ListBucket, logs), no EventBridge schedule to invoke it every minute.
- Model not uploaded to S3 yet (`models/trajectory.onnx` + `models/trajectory_norm.json` don't exist there) - and no model is finalized to upload yet either
  (variant B core+extras, runs/full_B, is the current best CANDIDATE, but the destination-feature question from earlier today is still open).
- Ingest Lambda memory bump (256 -> 512 MB, recommended above) not applied.
- VRS wake-class/military lookup not vendored into the predict Lambda yet (`_static_meta()` only computes category_id live; wake_id is hardcoded -1 = unknown
  for now - a small accuracy gap, not a crash risk, the model already handles wake_id via its embedding).
- `terraform plan`/`apply`, `docker build`/`push` - nothing run. No AWS credentials or billing touched by anything in this entry.

### 2026-09-28 LOCAL live test harness built and verified against REAL adsb.lol data (owner: "pehle local test krle, terraform baad me")
Agreed with the owner: test the real Lambda code locally before writing any AWS wiring. Built `ml/scratch/local_live_test.py`:
- Runs the ACTUAL `infra/terraform/lambda_ingest/handler.py` and `predict/handler.py` code UNMODIFIED (loaded by file path so both same-named
  `handler.py` files coexist) - only their `s3` client is swapped for a `LocalFileS3` stand-in that reads/writes plain files under `data/live_test/`
  instead of an S3 bucket (same get_object/put_object shape, so nothing in either handler needed to change). boto3/botocore are stubbed (neither is
  installed on this Mac) since nothing here ever calls real AWS. The model is loaded straight from `--model-dir` (default runs/full_B) on disk instead
  of from S3. A clean run is therefore real evidence the Lambda code itself works, not just this harness - only IAM/EventBridge/the container build
  are untested (deploy-only, left for later).
- Found and fixed a genuine LOCAL-MACHINE-ONLY bug while verifying: this Mac's python.org build has no root certs, so `urllib` HTTPS calls fail with
  CERTIFICATE_VERIFY_FAILED (the same issue already in memory `fish_shell_bash_tool_gotchas.md`). Fixed inside `local_live_test.py` only (points
  urllib's default HTTPS context at `certifi`'s CA bundle) - the real handler.py files were NOT touched, since this is a dev-machine quirk, not a bug
  in the Lambda code (the real Lambda runtime has proper certs).
- VERIFIED against the real live feed (one real fetch, 9.8 s): 2,973 real aircraft returned, 91% (2,696) had usable ML extras (airborne, valid
  position, non-`~` address) - close to the ~90-96% field-presence numbers measured on the GitHub history data, a good live/offline consistency check.
  Also removed an unused `import io` from `predict/handler.py` found while wiring this.
- Not yet run for real: the full `--minutes 25-30` loop (first predictions need ~10 min of history to build, first evaluations need ~5 more minutes
  after that) - this is the next command, run by the owner via run_logs/run.sh like every other long-running script in this project. Output: a summary
  comparing live-evaluated error to the offline test-set numbers (mean 3.29 km, median ~1.0, p90 ~9.4-10.0 for variant B).

### 2026-09-28 owner's dashboard v2 spec (refined) + corridors rebuild request - QUEUED, not started (website/frontend work is explicitly later)
Owner confirmed this was discussed before (matches decision D19/D20 in this journal's section 3-4) and added concrete detail. Recording the FULL spec here so nothing
is lost before frontend work actually starts:

**Map and general feel**
- "Industry standard" everywhere - backend AND frontend, the map itself (confirms D19: MapLibre GL + own vector tiles, not Leaflet/OSM tiles), smooth, no lag,
  professional-grade look - not a demo-quality map.
- When frontend work starts, explicitly invoke this account's frontend design/animation skills (e.g. `impeccable`, `animate`, a `design-taste-frontend` style
  skill - whichever is available at build time) rather than hand-rolling generic CSS - the owner wants this to look deliberately designed, with real animation
  decisions, not default/templated.

**Click-to-focus (confirms + extends D20)**
- Click one aircraft -> ALL other aircraft go blurred/faded (not just "removed").
- Any open dashboards/side-panels auto-close the moment an aircraft is clicked (declutter first, then show the one thing that matters).
- The clicked aircraft's detail panel appears with real animation (not an instant snap) and shows, "bhot cool tareeke se": where it came from and where it's
  going (VRS scheduled route, D21), the actual path it has flown so far (its real trail, from live/history), and — the actual ask for the ML side of this
  page — the model's prediction from ~10 minutes ago vs what ACTUALLY happened since, with the deviation between them made visually obvious (this is exactly
  what `local_live_test.py`'s evaluation loop now proves works end to end, offline; the dashboard needs to show the SAME comparison live, per aircraft, on click).

**New, not previously recorded**
- A "fastest aircraft right now" stat/leaderboard (uses `gs_ms`, already captured).
- A "compare two aircraft" view - pick two, see their speed/altitude/etc. side by side. Not designed yet; needs its own small spec when frontend work starts
  (which two fields matter most, live-updating or a snapshot compare).

**Corridors: REBUILD REQUESTED, delete the old one**
- `ml/scratch/train_all.py` + `ml/corridors.py` already implement corridor mining (DBSCAN-style, data-driven eps, heading features, airport-endpoint
  calibration) - built once before on the OLD 30-day S3 bronze data (which was India-then-Europe mixed, see `liveflights_industry_tier1` memory) and served
  today from `models/corridors.json` via the API's `/api/corridors`.
  Owner wants this REDONE on the new, richer Round 1 GitHub extract (`data/globe_extract_v5`, 11 Europe-only days, 71M rows, all the extra fields) instead,
  and the OLD `models/corridors.json` deleted, not kept alongside - a clean rebuild, not an addition.
  Plan (not started): re-run (or adapt) `train_all.py`'s corridor-mining logic against `data/globe_extract_v5` instead of the old bronze source, verify corridor
  count/quality (the code's own comments mention a bug it already fixed once - "only 26 corridors, one holding 23% of all traffic" - re-check this doesn't
  recur on the new source), then delete the current `models/corridors.json` and upload the new one - an S3 write, so this needs the owner's go before deploying
  (same AWS boundary as everything else today), but the MINING itself can be done and verified locally first, same pattern as everything else built today.

STATUS: nothing above is built yet. Frontend/dashboard work and the corridors rebuild are both explicitly "baad me" (owner's own words earlier: "website ka
kaam baad me hi karenge") - queued here so the eventual build has the full spec and doesn't have to be re-derived or re-asked.

### 2026-09-28 20:37 first local live test FINISHED (30 min, run live_test, exit 0) - found + fixed a real NaN bug, first real live numbers
Ran clean against the real live adsb.lol feed (no AWS): 2,700-3,000 real aircraft seen every minute throughout; predictions started at minute 10 (as expected);
evaluations started at minute 15 (as expected); 346 predictions were evaluated in total by minute 30.

BUG FOUND (from the summary printing "mean error nan km"): 23 of 346 (6.6%) evaluated predictions were NaN. Root-caused by inspecting the raw
`data/live_test/live__predictions.json`/`live__history.json` directly: aircraft `401b29` (and similar) reported EVERY reading with `gs` (ground speed) and
`track` both missing from the raw adsb.lol row - real live traffic (likely a helicopter/ultralight hovering or moving too slowly for the transponder to
compute a track), not a parsing bug. `ml/features.py`'s `window_x()` masks the OPTIONAL extras (roll/wind/etc.) when missing but does NOT nan-guard the two
CORE fields gs/track (never needed to before, because the offline extractor always interpolates them from neighbouring real trace points within 30 s -
this exact situation cannot happen in the training data). A live per-minute reading has no such neighbour to interpolate from, so a missing gs/track passed
straight into the model as NaN and turned the ENTIRE predicted path to NaN; a single NaN error then poisoned `_update_metrics`'s running `sum_km` for the
whole day (`+=` with one NaN makes the running total NaN forever after).
FIXED in three places, each verified with a targeted test against the exact failure shape:
  1. `ingestion/schemas/adsb_lol_extras_mapping.py`: `map_to_ml_fields` now also returns None (drops the reading) if `gs` or `track` is missing - the real,
     root-cause fix, matches the existing pattern for on-ground/no-position/non-ICAO rows.
  2. `predict/handler.py`'s `_select_window`: defense in depth - skips a window if gs/track are NaN anywhere in it, in case any other future upstream gap
     slips through (verified: correctly skips the bad shape, correctly keeps a good one).
  3. `predict/handler.py`'s `_update_metrics`: filters NaN out of `errors_km` before aggregating, so one bad value can never poison the whole day again
     (verified: mix of [1.0, 2.0, nan, 3.0] correctly aggregates to n=3, mean=2.0, not NaN).
Lesson: this is exactly the kind of gap that only shows up against REAL live data, not the offline GitHub-history test set - confirms the value of building
and running `local_live_test.py` before writing any AWS wiring, as the owner asked.

REAL FIRST LIVE NUMBERS (from the 323 valid, non-NaN evaluations already on disk before the fix; `data/live_test/metrics__daily.json`):
  mean 5.28 km, median 1.68 km, p90 13.12 km, p99 45.58 km (+5 min horizon)
Compared with the offline test set (variant B, full data, 100k windows): mean 3.29, median ~0.97-1.02, p90 ~9.4-9.96 km - live numbers are noticeably WORSE
across the board (mean +60%, median +65-73%, p90 +32-40%). Read with real caution, not alarm: n=323 vs the offline test's 100,000 is a small, noisy sample
(one 30-minute slice of whatever mix of level/turning/climbing traffic happened to be flying, not a balanced day); live per-minute polling has real jitter
`_select_window` tolerates (+-15 s) that the offline 10 s-grid extractor does not have; and this was the very first-ever live run, with no attempt yet to
check whether the live-caught traffic mix happened to be unusually hard (more turns/climbs than average). NOT concluded yet whether this gap is real
(a genuine live/offline distribution shift worth investigating) or just small-sample noise - a longer live run (2+ hours, more evaluated samples) is needed
before drawing a real conclusion. Next: re-run `local_live_test.py` for longer once the fix is deployed (data/live_test/ should be cleared first, or just let
the old NaN-containing history entries age out of the 15-minute window naturally - they already have by now).

### 2026-09-28 destination level-cruise fix implemented (owner: "destination features bhi daal de, model optimize kaise karein") - GATED destination, not yet retrained
Implemented the fix option (b) already planned in the 09-28 destination-experiment entry: `ml/features.py` FEATURES_VERSION 2 -> 3.
- New `not_level_gate(trk_deg, vr_baro, vr_geom)`: True if the window's own last 3 minutes show a turn (>8 deg, same threshold as
  `cand_regime()`'s regime==1) or a climb/descent (>2.5 m/s, same threshold as regime==2) - computed ONLY from data already in every window
  (train or live), so serving can compute the identical gate with no extra lookups. VERIFIED with a small synthetic test (level/turning/
  climbing/small-wobble cases all classified correctly).
- `window_s_dest()` now ANDs its existing `known` (destination found) with this gate - destination columns are zeroed (as if unknown) during
  ordinary level cruise, left exactly as before during a turn or climb/descent (the two strata where the ungated version helped).
- `build_windows_v2.py` updated to pass trk_deg/vrate history into `window_s_dest()`.
NOT YET DONE: rebuild the dest dataset with this gate (new output dir, keeps data/gru_v2_dest as the ungated reference), retrain variant B on
it, compare three-way against runs/full_B (no dest) and runs/full_B_dest (ungated dest): expect level-cruise median back near runs/full_B's
0.63 km (not the ungated dest run's 0.83 km) while turning/climb stay close to the ungated dest run's gains (turning mean 5.11 vs no-dest's
5.96). If level cruise is fixed and turning/climb gains hold, this becomes the new default model to wire into predict Lambda's S-width-14 path
(also not yet wired) and eventually into `models/trajectory.onnx` on S3.

### 2026-09-28 origin+destination route lookup added, for the dashboard (owner: "sab extract kar, click par pata hona chahiye")
- New `ml/route_lookup.py`: `load_route_lookup()` -> callsign -> {origin, destination, stops} (each {code, name, city, iata, country, lat, lon}), from
  VRS `routes.csv`'s full "ORIGIN-...-DESTINATION" `AirportCodes` string (94% simple 2-airport, ~6% with one or more stops) joined with `airports.csv`.
  Pure stdlib (csv module only, no pandas) so it drops into the predict Lambda without a new dependency. VERIFIED against real callsigns: DLH123 ->
  Munich (EDDM/MUC) -> Frankfurt (EDDF/FRA), BAW406 -> London Heathrow -> Valencia, both real routes; a light aircraft with no scheduled route
  correctly returns None. Load time ~2s for the full 620,700-row table. DISPLAY ONLY - does not feed the model (the model's own destination signal,
  when used, is the gated dest_dist_norm/sin/cos in ml/features.py, computed separately from just the destination end for the bearing/distance math).
- FOUND AND FIXED A REAL BUG while wiring this: `live/history.json` never stored callsign or category at all (only the numeric HISTORY_FIELD_ORDER
  fields) - so `predict/handler.py`'s `_static_meta()` was ALWAYS reading a missing "category" key and silently defaulting cat_id to 24 (unknown)
  for every single prediction, from the very first version of this Lambda, not something the recent refactor introduced. Also meant no route lookup
  was possible at all (no callsign to look up).
  FIXED: `live/history.json`'s per-aircraft entry is now a small nested object `{"callsign", "category", "type_code", "readings": [[...]]}` instead
  of a bare list of readings - identity fields updated to the latest seen each poll, `readings` unchanged (same trim/staleness logic as before).
  Updated `lambda_ingest/handler.py`'s `_update_history()`, and `predict/handler.py`'s `handler()`/`_eval_pending()` to match the new nested shape.
  `predict/handler.py` now loads the route table once per cold start (`_load_routes()`, same pattern as the model) and attaches each prediction's
  `route` field from the real callsign - VERIFIED end to end (real ONNX model, 12 synthetic DLH123 readings): prediction correctly produced, category
  correctly read as "A3" (was silently always 24 before), route correctly resolved to Munich->Frankfurt, matching the standalone route_lookup.py test.
- `predict/Dockerfile` updated: bundles `data/vrs/routes.csv` + `airports.csv` (~22 MB, CC0) directly into the container image rather than an S3
  round trip every cold start.
- Re-ran `local_live_test.py`'s wiring dry-run after all these changes: still loads/imports/predicts cleanly.
- STILL NOT DONE: wake_id (military/wake-turbulence class) is still a hardcoded placeholder in `_static_meta` - the VRS model-type/code-blocks tables
  are not vendored into the predict Lambda yet, separate from this route-lookup work. Dashboard display of the route (map arc, click-to-show) is
  still frontend work, queued with the rest of the dashboard v2 spec above - this entry only makes the DATA available in `live/predictions.json`'s
  `route` field, ready for a future API/frontend to read.

### 2026-09-28 21:10 GATED destination result: CLEAN WIN, best model so far (run train_full_B_dest_gated, exit 0, 788 s; runs/full_B_dest_gated/) -> NEW DEFAULT CANDIDATE
Test, +5 min error (km), three-way: no-dest (runs/full_B) vs ungated dest (runs/full_B_dest) vs GATED dest (runs/full_B_dest_gated):
| stratum | no-dest mean/median/win% | ungated dest | GATED dest |
|---|---|---|---|
| all | 3.29 / 0.97 / 66% | 3.04 / 1.21 / 60% | **3.01 / 1.00 / 66%** |
| level | 1.97 / 0.63 / 60% | 2.04 / 0.83 / 52% | **1.98 / 0.62 / 62%** |
| turning | 5.96 / 3.15 / 70% | 5.11 / 2.77 / 70% | **5.09 / 2.74 / 70%** |
| climb/desc | 5.26 / 2.56 / 75% | 4.52 / 2.55 / 74% | **4.52 / 2.54 / 75%** |
| unseen aircraft | 3.39 / 1.02 / 66% | 3.14 / 1.24 / 60% | **3.10 / 1.02 / 66%** |
The gate fix worked EXACTLY as hypothesized: level-cruise median (0.62 km) and win rate (62%) are now BETTER than the no-dest baseline (not just "no
longer worse"), unseen-aircraft median back to 1.02 (matching no-dest, was 1.24 with the ungated version) - the level-cruise regression is fully gone,
not just reduced. Turning and climb/descent gains from destination are fully preserved (5.09/4.52 vs no-dest's 5.96/5.26, essentially identical to the
ungated run's 5.11/4.52). Overall mean (3.01) is now the BEST of all three variants tried today (better than both no-dest's 3.29 and ungated's 3.04),
and overall median (1.00) and win rate (66%) both match or beat no-dest while ungated dest was clearly worse on both.
DECISION: runs/full_B_dest_gated is the new best model candidate, superseding runs/full_B. Confirms the day's larger lesson: destination is a genuinely
useful signal, but only when GATED to the situations where it's actually informative (turning/climbing) - blindly adding a feature without checking
WHEN it helps vs hurts would have shipped a net-worse model on the majority (level-cruise) case.
NEXT: export runs/full_B_dest_gated to ONNX, re-verify parity, wire predict/handler.py to the S-width-14 dest-aware path (window_s_dest + not_level_gate,
both already in ml/features.py) using the callsign->route lookup already built today (ml/route_lookup.py, though that returns display info; the ML
feature needs load_dest_table()'s bearing/distance, a separate function in build_windows_v2.py - should be moved into ml/features.py or a shared
module too, currently duplicated between train-time and not-yet-built serve-time dest lookup - TODO, not done). Then re-run local_live_test.py with
--model-dir runs/full_B_dest_gated for a live comparison against today's earlier no-dest live run.

### 2026-09-28 dedup: build_windows_v2.py's load_dest_table() now reuses ml/route_lookup.py
Was a near-duplicate of route_lookup.py's own airports.csv+routes.csv join (written independently earlier the same day, before route_lookup.py existed).
Replaced with a 2-line wrapper over `load_route_lookup()`, taking just the destination end. VERIFIED: still resolves 620,700 callsigns (identical to
route_lookup.py's own standalone count) - no coverage regression from consolidating.

### 2026-09-28 21:20 predict Lambda wired to the dest-gated model - VERIFIED end to end
- `predict/handler.py`'s `_predict_one()` now branches on `norm["use_dest"]`: with the new `runs/full_B_dest_gated` model (S width 14), it calls
  `window_s_dest()` (feeding the callsign's real destination lat/lon from `route`, already loaded for display) instead of `window_s()`; a model
  without dest (S width 10, e.g. the earlier runs/full_B) is completely unaffected, same call as before. `route` itself is always attached to the
  prediction output regardless (display info), independent of whether the loaded model uses it.
- VERIFIED with the real trained ONNX model + real VRS route data (no AWS): (a) a turning synthetic DLH123 flight predicts successfully, route
  resolves to EDDF/Frankfurt; (b) a level flight with an unmatched callsign predicts successfully (route=None handled, no crash); (c) DIRECTLY
  checked the S array's destination columns via `window_s_dest()`: a level-flight window with a REAL known destination still gets m_dest=0 (fully
  gated off, dist/sin/cos all zeroed) exactly as intended; the same destination on a turning-track window gets m_dest=1 with sane
  dist_norm/sin/cos values. The gate is confirmed working correctly both at training time (09-28 20:47 entry) and now at live-serving time.
- `runs/full_B_dest_gated` (S-width-14, gated destination) is now the fully wired, tested candidate for `models/trajectory.onnx` on S3 - nothing
  uploaded/deployed yet (still needs the owner's go, per the standing AWS boundary), but the CODE path is complete and locally proven correct.
- Remaining before a live re-test with this model: none blocking - `local_live_test.py --model-dir runs/full_B_dest_gated` should already work
  as-is (it just loads whatever --model-dir points to and runs the real handler code, which now auto-detects use_dest from norm.json).

### 2026-09-28 21:46 second local live test finished (dest-gated model, 30 min, run live_test_dest, exit 0) - result unclear, NOT a fair comparison to run 1
- Heavier rate-limiting this run (more repeated 420/429/failed lines in the log) than run 1 -> fewer aircraft survived to evaluation: only 82 evaluated
  predictions (vs run 1's 323), predicted counts running ~1,700-2,600 (lower than run 1's ~2,100-2,500 throughout).
- Numbers (n=82, no NaN, verified real/fresh, not stale data): mean 5.28 km, median 3.34 km, p90 13.11 km.
- RUN 1 (no-dest, 20:08-20:38, n=323): mean 5.28, median 1.68, p90 13.12.
- RUN 2 (dest-gated, 21:17-21:46, n=82): mean 5.28, median 3.34, p90 13.11.
- The median is much worse in run 2. A quick bootstrap gives non-overlapping ~95% CIs for the two medians (run1 ~1.3-2.3, run2 ~2.6-4.6), so this is
  probably not pure sampling noise BETWEEN THE TWO SAMPLES THEMSELVES - but that does NOT mean the dest-gated model is worse live, because this was
  NOT a controlled comparison: the two runs happened over an HOUR APART, on completely different real air traffic (different aircraft, different
  phase-of-flight mix, e.g. more evening descents/approaches near airports by 21:17 than at 20:08) - a real, uncontrolled confound. n=82 is also
  still a small sample on its own.
- Two live-plausible, NOT YET INVESTIGATED explanations if the gap turns out to be real (not just the confound above): (a) live track_deg readings
  are noisier than the offline GitHub grid (rounding/sensor jitter); `not_level_gate()`'s >8deg/2.5m/s thresholds could flicker between "level" and
  "turning" classification more often minute-to-minute on live noisy data than on clean offline data, making the destination signal appear/disappear
  inconsistently in a way the offline-clean-data evaluation never exercises; (b) simple bad luck - a batch of genuinely hard traffic (descending into
  a busy airport around 21:17-21:46) happened to be caught in this particular window.
- HONEST STATUS: inconclusive. The right next test is a CONTROLLED comparison - run both models on the SAME live traffic window at once (two parallel
  local_live_test.py processes, same minute-by-minute data, different --model-dir and --out), not two separate 30-minute windows an hour apart. Not
  yet done. Until then, runs/full_B_dest_gated remains the better model OFFLINE (proven, large sample) but its live advantage is NOT yet confirmed -
  runs/full_B (no dest) is not disproven as the safer live default; neither is deployed to AWS yet, so no live production decision is at stake yet.

### 2026-09-28 predict Lambda terraform written + validated (owner: "haan likh de")
New `infra/terraform/lambda_predict.tf`: ECR repo (same lifecycle-policy pattern as lambda_api.tf), `null_resource` docker build/push (triggers on
sha256 of handler.py/Dockerfile/requirements.txt/ml/features.py/ml/route_lookup.py/data/vrs/{routes,airports}.csv - rebuilds when any of those change),
`aws_lambda_function` (package_type Image, 512 MB/60s, env MODEL_KEY/NORM_KEY/LAKE_BUCKET_NAME), its own `aws_scheduler_schedule` (same
`var.schedule_expression` = rate(1 minute) as ingest, reusing the existing shared `aws_iam_role.scheduler`), `aws_lambda_permission`.
`iam.tf` edited: new `aws_iam_role.lambda_predict` + policy (GetObject+PutObject on live/*, models/*, metrics/*; ListBucket; logs; X-Ray - same
403-vs-404 ListBucket reasoning as the existing ingest/api roles); `scheduler_policy`'s `resources` extended to cover both Lambda ARNs now.
VALIDATED (not deployed): `terraform init -backend=false` (downloads provider plugins only, no AWS account touched) then `terraform validate` - passed
clean across the whole repo, confirming no broken references introduced. `terraform fmt` applied. `terraform plan`/`apply` NOT run (those use real AWS
credentials against the live account - left for the owner to run, per the standing AWS boundary, even though plan itself is read-only).
STILL NOT DONE before a real deploy: models/trajectory.onnx + trajectory_norm.json not uploaded to S3 yet (from runs/full_B_dest_gated); budget alert
not raised; ingest Lambda's memory bump (256->512) not applied; api/cloud/app.py not yet extended with endpoints for the dashboard to read
predictions/pending/metrics/route (Phase 2 of the plan given to the owner). Docker must be installed/running on whatever machine runs `terraform apply`
(the null_resource build step shells out to `docker build`/`docker push`) - not verified available in this environment.

### 2026-09-28 API extended with 3 dashboard endpoints - VERIFIED against real captured live-test data
`api/cloud/app.py` (+PREDICTIONS_KEY/HISTORY_KEY/METRICS_KEY constants, no new dependencies):
- `GET /api/predictions` - all current predictions from live/predictions.json, lightweight (no trail).
- `GET /api/aircraft/{icao24}` - the click-to-focus endpoint: current state (live/latest.json) + actual recent trail (live/history.json, shaped to
  plain {ts,lat,lon} points, default last 15 min) + current prediction + route, combined in one call. `found=False` cleanly for an unknown/departed
  aircraft, no error.
- `GET /api/stats/accuracy` - rolling per-day accuracy from the permanent metrics/daily.json (the "how accurate are we" dashboard proof).
VERIFIED (not deployed - stubbed boto3/fastapi/mangum locally, no packages installed, no AWS call) against the REAL files captured by today's
live_test_dest run: predictions_live returned all 2,639 real predictions; aircraft_detail for a real icao returned found=True, a real 12-point trail,
its real prediction; stats_accuracy returned the real day's rollup (n=82, mean 5.278, p90 13.111 - matches the run's own summary exactly); an unknown
icao correctly returned found=False with no crash.
This completes Phase 2 (API) of the plan given to the owner. Phase 3 (frontend dashboard) remains, spec already recorded above.

### 2026-09-28 22:06 model uploaded to S3 (owner ran it) - s3://liveflights-prod-lake-922120357133/models/trajectory.onnx + trajectory_norm.json
First real AWS action of the deploy sequence. Budget bumped 5->10 first (see budgets.tf edit above). Bucket confirmed: liveflights-prod-lake-922120357133.
My first command attempt used fish syntax inside run.sh's bash -c wrapper and failed (exit 2, "syntax error near unexpected token '('") - run.sh always
executes via `bash -c "$CMD"` regardless of the interactive shell being fish; fixed to bash syntax ($(...) not (...)) and re-ran successfully.
Next: start Docker Desktop (owner, manual), then `terraform plan` to review before `terraform apply`.

### 2026-09-28 22:44 first terraform apply attempt FAILED - .dockerignore excluded the VRS data files, fixed and re-verified
`terraform apply -target=...` (scoped to today's actual work, excluding CORS/throttle/github_actions drift found during plan review - see the entry
above on those 3 unrelated changes) failed at the docker build step: `.dockerignore` excludes the whole `data/` directory (correct, avoids shipping
GBs of training extracts into every image), which also silently excluded the two small `data/vrs/{routes,airports}.csv` files predict/Dockerfile needs.
FIXED: added `!data/vrs/routes.csv` / `!data/vrs/airports.csv` negation exceptions to `.dockerignore`.
VERIFIED locally before re-running the real apply (no AWS/ECR touched): `docker build` succeeded with the fix; then ran the built image locally via the
Lambda Runtime Interface Emulator (`docker run ... -p 9001:8080` + POST to `/2015-03-31/functions/function/invocations`) - the Lambda bootstrap started,
imported onnxruntime/numpy/features.py/route_lookup.py successfully, and reached `s3.get_object()` before failing with NoCredentialsError (expected and
correct - no AWS credentials were given to the local test container on purpose). This confirms the image itself is sound; only real IAM credentials
(which the deployed Lambda's role provides) were missing here. Test image cleaned up (docker rmi) after.
Next: re-run the same scoped `terraform apply` command.

### 2026-09-28 23:02 DEPLOYED TO AWS - predict Lambda live, ingest+api Lambdas updated, budget guardrail on
`terraform apply -target=...` (scoped, run 4 times total due to a docker-push network timeout at attempt 2 and an interruption at attempt 3 - each
retry was safe/idempotent, ECR skips layers already uploaded) finally reported "Apply complete! Resources: 4 added, 1 changed, 0 destroyed" on the
last run; independent resource chains had already completed across the earlier partial attempts. VERIFIED with a follow-up `terraform plan` on the
full target set: "No changes. Your infrastructure matches the configuration." - confirms everything intended is now live:
- New: aws_lambda_function.predict (liveflights-prod-predict), its EventBridge schedule (every 1 min), ECR repo, IAM role, log group.
- Updated: aws_lambda_function.ingest (new handler.py: all-fields capture + rolling history), aws_lambda_function.api (3 new endpoints).
- New: budget guardrail (aws_budgets_budget.monthly_gross + SNS topic/policy) - this had apparently never been deployed before at all (plan showed it
  as "will be created", not "updated"), so this is the first time a spend alert has actually been active on this account, not just written in code.
Deliberately NOT applied yet (excluded from the -target list on purpose, per the earlier plan-review entry): API Gateway CORS tightening ("*" ->
specific origins), API Gateway throttle tightening (20/10 -> 10/5), and the github_actions IAM policy drift (would have wiped CI push/deploy
permissions - needs investigation, not blind apply). These remain as open items, not blocking today's ML deploy.
Also fixed along the way: `.dockerignore` was excluding `data/vrs/{routes,airports}.csv` (the whole `data/` dir is ignored on purpose; added 2 negation
exceptions), verified with a local `docker build` + running the built image via the Lambda Runtime Interface Emulator before the real apply succeeded.
NEXT: verify live behaviour for real (CloudWatch logs / S3 objects appearing) - this is the first time this code has run against ACTUAL AWS Lambda
(everything before was locally simulated with local_live_test.py, however faithfully). Watch for real cold-start memory/timeout issues, real S3
permission edge cases, and the live/history.json size estimate (~9.6 MB projected) actually playing out.

### 2026-09-28 23:05 REAL BUGS found in the first live Lambda invocations: timeout + OutOfMemory
CloudWatch logs (aws logs tail, real deployed Lambdas, first few real invocations):
- Ingest Lambda: working correctly - fetching 2,405-2,535 real aircraft/poll, 19-21s duration (well inside the 90s timeout), rate-limit retries
  behaving as designed. No issue.
- Predict Lambda: BROKEN on real AWS. First invocation: model loaded fine (7.3s init), then ran the full 60s timeout budget and got killed
  ("Status: timeout", Billed Duration 67297ms). Second invocation: "Status: error, Error Type: Runtime.OutOfMemory" (Max Memory Used: 512 MB, the
  full configured limit).
ROOT CAUSE (found by reading handler.py's own code, not yet re-verified against real hardware): `handler()`'s per-aircraft loop calls `_predict_one()`
separately for EVERY aircraft, and `_predict_one()` calls `sess.run()` (the ONNX inference) ONCE PER AIRCRAFT - for ~2,500 aircraft/minute, that's
2,500 separate ONNX Runtime calls, each with its own fixed per-call overhead, instead of ONE batched call with all 2,500 windows stacked together.
This was invisible on the Mac (fast CPU, `local_live_test.py` never timed out across two 30-minute runs) but Lambda's CPU allocation at 512 MB is far
weaker, and 2,500x the per-call overhead compounds badly. This is a real, important performance bug, not a data/model problem - the SAME mistake the
09-26 ONNX parity check already warned about implicitly (batch-size testing showed 2,500 in ONE call took 86ms on the Mac; the deployed code never
uses that batched path at all, only ever calling with a batch of 1).
FIX (planned, not yet done): rewrite `handler()`'s prediction loop to stack every aircraft's window into one batched X/S array and call `sess.run()`
once (or in a few large chunks, in case there's a Lambda payload/memory ceiling on one huge batch) instead of once per aircraft. Also raise Lambda
memory (512 -> likely 1024 or more) as a second, independent fix - 512 MB was already fully used even in the failing run, so it needs headroom
regardless of the batching fix. Must re-verify with `local_live_test.py` locally (correctness: batched output must exactly match the per-aircraft
output already verified before) before redeploying.

### 2026-09-28 23:20 BOTH real bugs fixed and verified locally - ready to redeploy
1. Batching fix (predict/handler.py): replaced the per-aircraft `_predict_one()` loop with `_predict_batch()` - stacks every aircraft's window into
   one array and calls `sess.run()` once per chunk (chunk_size=2000) instead of once per aircraft. VERIFIED correctness (batched vs one-at-a-time
   predictions match EXACTLY, 0.0 diff, on 20 real aircraft) and speed (2,639 real aircraft: 187ms batched vs ~1.2s projected one-at-a-time, on the
   same Mac where one-at-a-time already looked "fine" locally but still timed out on Lambda's weaker CPU). A try/except around the batch call falls
   back to one-by-one only if the batch itself throws, so one bad aircraft's data still can't sink the whole poll.
2. Memory fix (ml/route_lookup.py): the unfiltered route table (620,700 small nested dicts) used ~200-450 MB by itself, and total process RSS hit
   876-914 MB - alone enough to explain the OutOfMemory even without the batching bug. A first attempt (region_box filter on the existing
   load_route_lookup()) only cut it to 377,180 entries / 126-390 MB (not enough - Europe is too central a hub for a simple "touches the region"
   filter to help much). REDESIGNED: new `RouteTable` class + `load_route_table()` - stores only compact (origin_code, dest_code, stop_codes) STRING
   TUPLES per callsign (not per-route dict objects) plus the small ~34,128-entry airport dict, and expands to the full nested-dict shape lazily,
   only for the specific callsign a caller's `.get()` actually asks for (never all 377k at once). Measured: peak 114 MB (was 390-447 MB), total
   process RSS 251 MB (was 876-914 MB). `predict/handler.py` switched to `load_route_table()`; `build_windows_v2.py`'s training-time
   `load_dest_table()` untouched (still uses the original unfiltered `load_route_lookup()` - training runs on a normal machine with plenty of RAM,
   and deliberately needs the FULL global table since a Europe-transiting flight's destination can be anywhere on Earth).
END-TO-END VERIFIED (both fixes together, real captured data: 2,639 real aircraft from data/live_test_dest/live__history.json, real trained ONNX
model, real VRS route CSVs, local S3 stub only): `handler()` completed in 1.59s (vs the deployed version's 60s timeout), all 2,639 predicted
successfully, process RSS 327 MB (vs the deployed version's OutOfMemory at 512 MB).
NEXT: bump Lambda memory_size as an extra safety margin regardless (512 -> higher), then redeploy just the predict Lambda (image + function
resources) via terraform apply, then check real CloudWatch logs again for a clean run.

### 2026-09-28 23:17 REDEPLOYED FIX - CONFIRMED WORKING on real AWS (CloudWatch logs)
`terraform apply -target=null_resource.predict_image -target=aws_lambda_function.predict` succeeded in 85s (fewer layers to push this time).
Real CloudWatch logs (aws logs tail) after redeploy: last two old-code timeouts visible right at the deploy transition ("Loaded route lookup: 620700
callsigns" = old code), then all subsequent invocations show the new code ("Loaded route table: 377180 callsigns (region-filtered, lazy expand)") and:
  predicted=2414 skipped(no history)=556 evaluated=0 still_pending=2097 | Duration 7643-8905ms (cold, model+route load) then 2451-2697ms (warm)
  Max Memory Used: 422-445 MB (of 1024 MB) - no more OutOfMemory.
Both real production bugs are fixed and confirmed on actual AWS Lambda, not just local replay. The predict Lambda is now running cleanly every minute.
`evaluated=0` so far is expected (needs ~5 more minutes of real runtime for the first predictions to reach their target time) - next check should show
real evaluated counts and the first live metrics/daily.json entries from ACTUAL production, not local_live_test.py's simulation.
STATUS: liveflights trajectory-prediction ML pipeline is LIVE on AWS: ingest Lambda capturing all fields + rolling history, predict Lambda predicting
+evaluating every minute with the gated-destination model (runs/full_B_dest_gated), API extended with 3 new endpoints, budget guardrail active.
Remaining: Phase 3 (dashboard/frontend) - not started. Also still open: CORS/throttle tightening and the github_actions IAM policy drift found during
plan review (deliberately not touched today), VRS wake-class lookup not wired into predict Lambda, 7-day raw archive (decision D15) not implemented
separately from the 15-minute rolling history.

### 2026-09-28 23:25 eval log added (owner: "future predictions ka poora data save karo, model improve karne ke liye")
`predict/handler.py`: `_eval_pending()` now returns full RECORDS (icao, made_at, target_ts, predicted lat/lon, actual lat/lon, actual_ts, error_km,
had_route) per evaluated prediction, not just the bare error number `metrics/daily.json` already aggregated. New `_append_eval_log()` writes these to
`eval_log/<date>.json` (one small object per day, read-modify-write like every other object here - not a per-prediction S3 write), kept ~60 days
(EVAL_LOG_RETENTION_DAYS, owner said "kuch dino ke liye" - can extend/shrink once retraining actually uses this). `iam.tf` updated: predict Lambda's
policy now also covers `eval_log/*`. VERIFIED end to end locally (real ONNX model, synthetic predict-then-evaluate cycle): evaluated=1, eval_log got
exactly the expected record, metrics/daily.json updated too - both consistent. Not yet deployed (next command).
This complements, not replaces, metrics/daily.json (same-day aggregate for the dashboard's live "how accurate are we" number) - eval_log is the
per-prediction detail for later model work (e.g. checking whether a stratum like turning/dest-known systematically differs, or building a genuine
live-vs-offline comparison once enough days accumulate).

### 2026-09-28 late night: dashboard click-to-focus built (owner: "pehle dashboard kar, live link par ache se, industry standard")
Explored the EXISTING web/ dashboard first (Next.js 14, Leaflet/react-leaflet, dark theme, already a real app-shell with TopBar/LayerControls/
AnomalyFeed/ChartsPanel/Leaderboards - "fastest right now" leaderboard already existed) rather than rebuilding from scratch. Loaded the `impeccable`
design skill (context.mjs confirmed this is a scoped refinement of existing code, not a new-surface build) and its craft-floor quality checklist before
editing UI.
New/changed frontend files:
- `types/api.ts` + `lib/api.ts`: added AirportInfo/RouteInfo/PredictionRecord/AircraftDetailResponse/AccuracyResponse types and
  `api.predictions()/aircraftDetail()/accuracy()`, wired to the 3 endpoints added to api/cloud/app.py earlier today.
- `lib/geo.ts` (new): great-circle interpolation (slerp) so the scheduled-route line is a real curved arc, not a straight rhumb line - the way real
  flight trackers draw routes.
- `components/map/PredictionLayer.tsx` (new, replaces the old `GhostTrailLayer.tsx` - deleted, no longer referenced): draws the selected aircraft's
  REAL trail (solid), the GRU model's predicted 5-minute PATH (dashed, all 30 points, not one guessed endpoint like the old paused local-only
  trajectory model gave), and the scheduled origin-destination route (faint curved arc + airport dots with hover tooltips, always labeled
  "scheduled", never asserted as fact).
- `components/map/AircraftLayer.tsx`: click-to-focus fade - every aircraft except the selected one dims to 0.22 opacity, with a CSS transition
  (`globals.css`, exponential ease-out, "the one authored moment" per craft-floor).
- `components/map/DeselectController.tsx` (new): clicking empty map clears the selection (Leaflet markers already stop click propagation, so this
  doesn't fire on a marker click).
- `components/panels/AircraftDetailPanel.tsx` (new): slide-in panel (translate+fade entrance) - live state (altitude/speed/phase/squawk/emergency/
  military hint), scheduled route (origin/destination city names + distance, labeled as schedule data not a confirmed flight plan), and the model's
  prediction next to TODAY'S REAL LIVE ACCURACY NUMBER (`api.accuracy()`, from the predict Lambda's actual evaluation loop - not a claimed number).
- `app/live/page.tsx`: replaced the dead `api.trajectory()` call (an endpoint that only ever existed in the local-only FastAPI, never in the deployed
  cloud API - selecting an aircraft on the live site currently does nothing useful) with `api.aircraftDetail()`; selecting an aircraft now also
  auto-collapses AnomalyFeed and ChartsPanel (owner: "jo bhi dashboards khule hain wo band ho jaye"); the open panel/path re-fetches every 15s while
  selected (predictions update every real minute); Escape key and clicking empty map both close it.
- `components/panels/TopBar.tsx`: added a live "Predict acc (5min)" stat next to the existing KPIs, from the same real accuracy endpoint.
VERIFIED (not yet visually inspected in a real browser - needs the owner's Chrome permission first, per the standing rule): `npx tsc --noEmit` clean,
`pnpm build` succeeded (static export, 6 pages, /live route 212 kB first load), and the impeccable skill's mechanical detector returned zero findings
on all changed files.
NOT YET DONE: visual/browser check; deploying this build to the live site (S3 site_bucket - a real AWS action, needs the owner's go); the "compare
two aircraft" feature (deprioritized this pass, the click-to-focus + real prediction wiring was the larger, more clearly-requested piece); wake-class
still not wired into predict Lambda (separate, older gap); CORS/throttle/github_actions drift found during the earlier plan review still untouched.

### 2026-09-29 visual check found a real bug: API Lambda IAM missing metrics/* -> fixed
Ran the new dashboard locally (pnpm dev, pointed at the real deployed API) and opened it in Chrome per the owner's "dikha" - the TopBar's new "Predict
acc" stat showed "—" and a direct curl of `/api/stats/accuracy` returned a bare 500. Root cause from CloudWatch (`/aws/lambda/liveflights-prod-api`):
`botocore.exceptions.ClientError: AccessDenied ... s3:GetObject on ... metrics/daily.json` - the API Lambda's IAM role only ever had live/*, stats/*,
models/* (from before today), never metrics/* (added by the predict Lambda earlier today, but its own IAM statement, not the API Lambda's). This is
exactly the kind of gap local testing structurally cannot catch (local replay swaps out the S3 client entirely, so IAM is never exercised) - only a
real browser hitting the real deployed API surfaced it. Verified the other two new endpoints (`/api/predictions`, `/api/aircraft/{icao24}`) work fine
via direct curl - both only touch live/* which the API Lambda already had. Fixed: added metrics/* to the API Lambda's existing LiveStateRead IAM
statement in iam.tf. terraform validate clean. Not yet deployed (next command). Also noted, separately, unrelated to today's work: the map's CARTO
dark_matter tile layer shows a diagonal "API KEY REQUIRED" watermark across the tiles - the free CARTO endpoint this code comments say needs no key
apparently now does, or rate-limits without one. Pre-existing, not something today's changes touched - flagged for a separate fix, not blocking.

## 2026-09-29 - Duplicate popup fix, corridor decluttering, CARTO tile fix, compare-2-aircraft feature, live S3 deploy, and a real live-accuracy check

Picked up right after the dashboard build session ended. Owner said: fix the duplicate-popup bug (confirmed, see below), then decide next steps.
Gave four concrete open items and owner picked all of them plus the deploy: "2,3,4 sahi karde...uske baad live S3 pe deploy karde...aur predictions
aur model sahi se kaam kr rha hia na...yeh dekhle" (fix corridors/CARTO/compare, then deploy the dashboard to the live site, then verify the model's
real live accuracy).

**Duplicate popup bug - confirmed fixed.** Removed the old Leaflet `marker.bindPopup()` call, `popupHtml()`, and `derivePhase()` from
`AircraftLayer.tsx` (the new `AircraftDetailPanel` React component fully replaced it, but the old popup was still firing alongside it). Re-tested by
clicking a second, different aircraft (KLC76K, NUE->AMS) in the local dev build - only the new panel appeared, no duplicate.

**Corridor decluttering on select.** `CorridorLayer` takes a new `dimmed` prop; `FlightMap` passes `dimmed={Boolean(selectedIcao24)}`. Dimmed drops
corridor line opacity 0.45->0.12, centroid dot opacity 0.6->0.18, hub-spur opacity 0.5->0.15 - fades the whole-map corridor mesh without hiding it
outright (still useful spatial context) so a selected aircraft's trail/prediction/route lines read clearly against it. Verified via a controlled
before/after screenshot at the exact same pan/zoom (hard-reloaded the page first to rule out stale HMR state) - corridors visibly duller and aircraft
markers visibly faded once something is selected, same crop.

**CARTO tile watermark - root-caused, not just flagged this time.** Curled both of CARTO's anonymous tile CDN hosts directly
(`{s}.basemaps.cartocdn.com` and the older `cartodb-basemaps-{s}.global.ssl.fastly.net`) - both return HTTP 200 but the *same* watermark PNG
("API KEY REQUIRED - carto.com/basemaps/apikey") instead of a real tile. This is CARTO having fully retired free anonymous basemap access, not a
transient rate-limit or outage. Fix: swapped to Esri's World Dark Gray Canvas (`server.arcgisonline.com/.../World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`
- note the swapped y/x order vs every other provider) - free, no key, no signup, genuinely dark-styled with real labels (not a CSS-inverted light
basemap). Verified visually: clean tiles, no watermark, looks better than the old CARTO layer did even before it broke.

**Compare-2-aircraft feature - built.** Deprioritized in the original dashboard build, now built:
- `AircraftDetailPanel` takes `variant: "primary" | "compare"`, an `onCompare` callback, and `picking`/`onCancelCompare` - the primary panel shows a
  "Compare with another aircraft" button; clicking it arms `pickingCompare` state in `page.tsx`, changes the button to a
  "Click another aircraft to compare..." hint with Cancel, and the *next* aircraft click (in `selectAircraft`) fills the compare slot instead of
  replacing the primary selection.
- Two panels render side by side (compare panel at `right-[324px]`, primary stays at `right-3`); each has a small colored dot (cyan for primary,
  a new `accent.violet` (#a78bfa) tailwind token for compare) as the one visual anchor tying a panel to its marker/lines on the map - first tried a
  colored left border on the compare panel instead, but `impeccable`'s detector flagged it immediately as `[side-tab]`, "the most recognizable tell
  of AI-generated UIs" - removed it, kept only the dot.
- `AircraftLayer` now takes `compareIcao24` alongside `selectedIcao24`: both get the fade-immunity + outline treatment (compare aircraft outlined in
  accent-violet instead of accent-cyan), every other aircraft fades same as before.
- `PredictionLayer` takes an `accent` color prop (defaults to cyan); `FlightMap` renders it twice when `compareDetail` is set, once per color, so the
  two aircraft's dashed prediction paths + predicted-position dots are visually distinguishable on the map, not just in the two panels.
- Edge case handled: clicking the aircraft already in the compare slot (outside picking mode) clears compare instead of leaving both slots pointing
  at the same aircraft. Escape key backs out one level at a time (cancel picking -> close compare -> close everything).
- Verified end-to-end in the local dev build: selected MBU3UM (HAM->PMI), clicked Compare, clicked UAL70 (EWR->AMS, descending into AMS) - both
  panels appeared side by side with correct colors, only the primary panel kept its Compare button, and UAL70's violet trail/prediction rendered
  correctly near AMS on the map.
- `tsc --noEmit` clean and `impeccable detect` clean (0 anti-patterns) on every changed file before calling it done.

**Live S3 deploy - done, with one real bug caught and fixed in the process.** Built the static export (`next build`, `output: "export"`) and ran
`aws s3 sync out/ s3://liveflights-prod-site-922120357133/ --delete`. First deploy attempt shipped broken: the live site showed
"Could not reach the API at http://localhost:8000" everywhere. Root cause: `.env.local` (local-dev-only overrides: `NEXT_PUBLIC_API_BASE_URL=
http://localhost:8000`, `NEXT_PUBLIC_DEFAULT_REGION=india`) always outranks `.env.production` in Next.js's env-file precedence, even during a
production build - so the production static export had the local FastAPI URL baked into it instead of the real API Gateway URL. Fixed by moving
`.env.local` aside, rebuilding (confirmed via grepping the built JS chunk for `execute-api.us-east-1.amazonaws.com` instead of `localhost:8000`),
restoring `.env.local` immediately after, and re-syncing to S3. Verified on the actual live URL
(`http://liveflights-prod-site-922120357133.s3-website-us-east-1.amazonaws.com/live.html`): real live data (1,482 aircraft, 45 countries), clean
Esri tiles, clicked a real aircraft (TUI35X, DUS->HER, climbing through 30,525ft) and got a working detail panel with a live GRU prediction ("+5 min
target: in 3 min") and today's live accuracy stat. **Note for next deploy: this `.env.local`-precedence trap will recur on every future production
build unless something changes** - worth either renaming the local-dev file to something Next.js won't auto-load, or adding a `pnpm build:prod`
script that unsets/overrides `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_DEFAULT_REGION` explicitly via shell env (which wins over every `.env*`
file in Next's precedence order) before calling `next build`.

**Live model-accuracy check - genuinely good typical accuracy, but a real tail-outlier bug found in the eval pipeline (not the model).** Pulled
`metrics/daily.json` and `eval_log/2026-09-29.json` straight from S3 (read-only) rather than trusting the dashboard's single mean-km stat:
- Per-day aggregate mean is ~4.9-5.3 km for a 5-minute-ahead prediction (09-28: n=4241, mean 4.87km; 09-29 so far: n=1093, mean 5.28km) - looks fine
  but is skewed by a small number of very large errors (std 13.5-23km, max errors 231-338km).
- The *median* (computed from the up-to-2000-sample reservoir each day stores) tells the real story: **1.29km on 09-28, 0.97km on 09-29** - excellent
  for a 5-minute horizon at typical cruise speed (a 799 km/h aircraft covers ~66km in 5 minutes, so a ~1km median error is roughly 1.5% relative).
- The worst individual `eval_log` records (e.g. icao `ae0568`: predicted near Ostend, actual position 338km away near Aachen, both timestamped ~5
  minutes apart) imply speeds of 3,000-4,000 km/h, physically impossible for any aircraft in this dataset. This means those specific records are
  very likely an **eval-matching bug, not a model failure**: `_eval_pending()` in `predict/handler.py` looks up the "actual" position purely by
  `history.get(icao)`, with no sanity check that the reading it finds is remotely plausible given the aircraft's own recent motion - if an icao24 hex
  gets briefly reused/collided (a known ADS-B data-quality issue, not unique to this pipeline) between the prediction and the 5-minute eval window,
  the eval silently compares the original aircraft's prediction against a *different, unrelated* aircraft's position and logs a huge fake error.
- **Not fixed yet** - flagged to the owner rather than redeploying the predict Lambda unasked (AWS-touching change). Proposed fix if wanted: add an
  implausible-speed guard in `_eval_pending` (discard/skip a match whose implied speed from the aircraft's last known reading to the "actual" reading
  exceeds a generous real-aircraft ceiling, e.g. ~1200 km/h, rather than counting it as model error) - a same-file change, no terraform, just a
  predict-Lambda image rebuild + redeploy.
- Bottom line to relay honestly: the model itself is working well (median ~1-1.3km @ +5min, matches/beats the training-time numbers from the
  destination-gating work); the dashboard's single "mean" stat is a misleading way to present that because of this separate eval-log contamination
  issue, worth switching the displayed stat to median once the eval bug is fixed, and worth filtering these contaminated records out before ever
  using `eval_log/*` to retrain.

**Eval-matching bug - fixed and redeployed same session (owner said yes to the fix+redeploy).** Added a plausibility guard to `_eval_pending()` in
`predict/handler.py`: before counting a match as evaluated, derive the implied speed from the aircraft's own `start_lat`/`start_lon` (recorded at
`made_at`, already stored in every pending record - no new field needed) to the candidate "actual" reading; if that implies more than
`MAX_PLAUSIBLE_SPEED_KMH = 1200` km/h, drop the match entirely (not counted as an error, not kept pending either - the data for that icao at that
time is corrupted, waiting longer won't fix it). Verified the logic with a standalone unit test before deploying (stubbed `boto3`/`onnxruntime`,
pointed `sys.path` at `ml/` for the vendored `features`/`route_lookup` imports, fed `_eval_pending` one normal case and one synthetic
impossible-speed case) - normal case evaluated correctly, impossible case dropped from both `records` and `still_pending`, exactly as intended.

Redeployed via the same `terraform apply -target=null_resource.predict_image -target=aws_lambda_function.predict` pattern used earlier this session
- image pushed, Lambda updated, `terraform plan` afterward showed "No changes" (no drift introduced). Verified for real, not just assumed:
- CloudWatch logs (`aws logs tail`) show the new cold start at 05:08:18 UTC loaded cleanly (model, route table, no errors) and every invocation since
  has run normally (Duration ~2-10s, Memory ~375-420MB of 1024MB, `evaluated=` counts staying small and sane).
- Pulled `eval_log/2026-09-29.json` again after the redeploy and split records by `target_ts` relative to the redeploy timestamp: **all 9 of the
  file's >150km "impossible" records predate the redeploy; zero new ones have appeared since** (checked both by `made_at` and, more precisely, by
  `target_ts` - no post-redeploy record has come due for eval yet as of the check, but the live `evaluated=` counts in the logs already confirm the
  guard is running every cycle without crashing).
- Old contaminated records already in `eval_log/*.json` are NOT retroactively cleaned (the log is additive) - if `eval_log` is ever used to retrain,
  filter records where the implied start->actual speed exceeds ~1200 km/h before trusting them, same guard as the live check.

**First-ever push to GitHub, and CI immediately caught real pre-existing debt.** Owner asked "github par push commit kiya????" - answer was no: this
entire multi-session ML/dashboard effort (predict Lambda, ml/features.py, ml/route_lookup.py, ml/scratch/*, the whole rebuilt dashboard) had only
ever existed locally, never committed. Fixed properly rather than as one dump:
- `.gitignore`/`docs/gitignore-recommended.txt` had silently drifted apart (someone had edited `.gitignore` directly in an earlier session without
  syncing the "recommended" doc back) - synced them, and added entries for `runs/` (trained model checkpoints, regenerable, same category as the
  already-ignored `ml/scratch/artifacts/`), `ml/scratch/artifacts_prev_2026-09-09/` (an old dated backup of the same), and `run_logs/*.log` +
  `run_logs/INDEX.txt`/`LATEST.txt` (keeping `run_logs/run.sh`, the actual reusable runner script, tracked).
- Branched (`ml-dashboard-live-2026-09-29`, not main) and split the work into 3 readable commits by area (ML/predict-Lambda backend, dashboard
  frontend, docs) rather than one giant commit.
- First CI run on push: 2 real failures, both genuine pre-existing debt that simply never got checked before (this code was never pushed, so CI
  never ran on it) - `ruff check .` (39 line-length violations across ml/features.py, ml/route_lookup.py, predict/handler.py, api/cloud/app.py -
  all of it written across prior sessions without ever running the repo's own lint rule) and `terraform validate` (lambda_predict.tf's
  `null_resource.predict_image` called `filesha256()` directly on `data/vrs/routes.csv`/`airports.csv`, which are gitignored - absent on a fresh CI
  checkout, so validate crashed trying to hash a file that isn't there).
- Fixed both: reflowed the 39 long lines (verified no logic changed - a standalone test of the eval-pending plausibility guard and `_static_meta`
  still passed, plus the existing 58-test pytest suite, plus a plain `ast.parse` syntax check on every edited file); wrapped the two VRS-CSV hashes
  in `try(..., "missing-in-ci")` (verified both ways - moved the CSVs aside and confirmed `terraform validate` passes without them, then restored
  them and confirmed it still passes with the real files, so local applies keep real change-detection). Pushed as a third commit; the next CI run on
  the branch came back fully green (dbt/audit/terraform/api-image/test all ✓).

## Update 2026-09-29 (later) - PR opened, median accuracy stat, safe prod-build script, terraform drift reconciled

Owner: "PR khol de....aur sab kuch theek karde" (open the PR, and fix everything else that was flagged as pending).

- **PR #17 opened** (`ml-dashboard-live-2026-09-29` -> `main`), CI green on it.
- **Dashboard's accuracy stat switched from mean to median.** Flagged earlier today as misleading (09-28's mean was ~4.9km but median was ~1.3km -
  mean gets dragged up hard by rare large-error outliers, median barely moves). Added `median_km` to `predict/handler.py`'s `_update_metrics`
  (computed the same way `p90_km` already was, from the same `sample_p90_km` reservoir - the function's own docstring already said "mean/median/p90"
  but median was never actually implemented until now). `api/cloud/app.py`'s `/api/stats/accuracy` falls back to deriving `median_km` from the
  stored `sample_p90_km` array for any day written before this field existed, so old days don't have a hole in the chart - no backfill script
  needed. `TopBar.tsx` and `AircraftDetailPanel.tsx` now display `median_km`. Redeployed both predict and api Lambdas (`terraform apply -target`
  x4, ~10min for the docker builds+pushes - `api_image`'s push alone took ~9min, slower than usual, no obvious cause, not investigated further
  since it completed fine); verified via CloudWatch logs (both healthy, no errors) and a direct curl of the live `/api/stats/accuracy` endpoint -
  confirmed real numbers: 09-28 `mean_km=4.866`, `median_km=1.289`.
- **`.env.local`-vs-`.env.production` precedence trap - properly fixed this time**, not just documented as a manual workaround. New
  `web/scripts/build-prod.sh` + a `build:prod` package.json script: sources `.env.production` and exports its values as real shell env vars before
  calling `next build` - shell-exported vars win over every `.env*` file in Next's own precedence order, so this works correctly even with
  `.env.local` present, no more moving it aside by hand for every deploy. Verified: ran `pnpm build:prod` with `.env.local` present and confirmed
  the built JS chunk still had the real API Gateway URL, not `localhost:8000`.
- **The 3 terraform drift items flagged much earlier this session - investigated for real, not left untouched.** `terraform plan` showed only 2 (the
  third, a github_actions IAM policy risk, turned out to have no actual drift when checked - nothing to do there). Both real ones turned out to be
  *live AWS deliberately ahead of code*, not accidental drift to blindly revert:
  - **Throttle limits**: live had burst=20/rate=10, code said 10/5. This matches this exact session's own earlier finding - the tighter limit is
    the most likely cause of the real, reproducible `/api/flights/live?limit=6000` slowness hit while testing the new dashboard - someone had
    already fixed it directly in AWS console. Updated the code to match reality (20/10) instead of reverting a fix that was already working; a
    `terraform plan` afterward showed this item clean.
  - **CORS**: live had `allow_origins=["*"]` (wildcard), code had a specific 3-origin list. The code's own comment says CORS isn't the real access
    boundary (API is public read-only, throttle bounds abuse) - but a wildcard is still a needless loosening worth tightening back. Left the code
    as the narrow list (correct target state) but **could not apply it** - this environment's own permission classifier blocked the
    `terraform apply -target=aws_apigatewayv2_api.api` call outright as a "Protected-Scope IaC Apply" and explicitly instructs not to route around
    that kind of denial through another tool. Reported this honestly rather than finding a workaround; the owner needs to run it themselves:
    `cd infra/terraform && terraform apply -target=aws_apigatewayv2_api.api`.
- Rebuilt the web dashboard with `build:prod` and re-synced to the live S3 site; verified the live URL serves the new build (median stat present in
  the deployed JS, confirmed via curl).
