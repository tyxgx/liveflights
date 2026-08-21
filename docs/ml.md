# ML

**Design principle: rules catch physically impossible, ML catches contextually unusual.** Silver's `data_quality_flags` already threshold implausible speed, altitude, vertical rate, missing position, and emergency squawks — every state a point-wise anomaly model would learn to flag. The original plan called for an IsolationForest here; it was rejected as circular reasoning; training it on the same features `data_quality_flags` already thresholds would just rediscover those same rules with none of their interpretability and all of the training cost. The actual gap rules can't fill: a flight can be legal on every single dimension — plausible speed, altitude, vertical rate — and still be behaviorally strange: off the path everyone else takes between the same two points, against the flow of traffic, at an altitude nobody else uses on that route. That's a population-relative judgment, not a fixed bound, so it needs a model that has seen the population. That's what the four models below do.

## Corridor discovery (DBSCAN)

`ml/corridors.py` clusters scaled `latitude`, `longitude`, `sin(true_track)`, `cos(true_track)` — heading is included specifically so opposing traffic on the same lat/lon airway separates into two corridors instead of merging into one. Filtered to airborne, cruise-phase points. `eps` is chosen per fit via a k-distance elbow (max perpendicular distance from the line joining the sorted-distance curve's endpoints).

**Fit per region, not combined** — a single `StandardScaler`+DBSCAN fit across Europe and India (thousands of km apart) distorts the shared scaled feature space and collapsed silhouette from 0.607 to 0.113 in an early run. Each region now gets its own scaler, its own k-distance elbow, its own `eps`.

**Current state**: 181 corridors (180 India, 1 Europe — see [limitations.md](limitations.md) on why Europe is now token-sized), India silhouette **0.2016**. Silhouette is a genuinely poor metric for this problem — it assumes globular, well-separated clusters, while flight corridors are elongated and linear along a shared heading, which the metric structurally penalizes regardless of whether the clustering is correct. Validation was geometric instead: corridor endpoints were checked against real airport coordinates (one corridor's start point landed within 0.1° of Bengaluru's actual coordinates; others traced plausible Maharashtra→Haryana and Andhra-coast→Kolkata-shaped routes). Worth noting directly: cleaning stale simulator rows out of silver (see [engineering-notes.md](engineering-notes.md)) measurably raised India's silhouette from -0.017 to 0.2016 — data quality improved model quality, not tuning.

## Trajectory prediction (gradient boosting on deltas)

`ml/trajectory.py` predicts `delta_lat`/`delta_lon` five minutes ahead — as deltas, not absolute coordinates — from current position/velocity/heading plus turn-rate, acceleration, and climb-trend computed over the last observation. Compared against a mandatory dead-reckoning baseline (great-circle projection along current track/speed).

| phase | n | model median (km) | model p90 (km) | dead-reckoning median (km) | dead-reckoning p90 (km) |
|---|---|---|---|---|---|
| cruise | 2,106 | 2.455 | 5.118 | 9.300 | 15.280 |
| climb | 103 | 2.269 | 4.626 | 10.920 | 15.806 |
| descent | 83 | 1.760 | 4.897 | 9.482 | 14.693 |
| **overall** | **2,292** | **2.422** | **5.109** | **9.425** | **15.303** |

The model wins in every phase, including cruise — the honest explanation is that it implicitly denoises the simulator's own per-reading noise (`velocity`/`vertical_rate` each get independent random jitter every tick, on top of deterministic great-circle motion): dead reckoning extrapolates from one noisy instantaneous sample, while the model, trained across thousands of examples, regresses toward the true expected speed. This was re-verified after fixing the dead-reckoning baseline to use each pair's actual elapsed time instead of an assumed fixed 300s (more physically correct, and a check against the win being a baseline artifact) — on a larger set (n=4,394) the model still won overall (median 2.489km vs 9.004km) and in cruise specifically (2.520km vs 8.972km), so the result held. **Limitation stated plainly**: all 21,969 valid (t, t+5min) pairs are `simulate`-sourced — zero are real OpenSky, because the real capture is a single point-in-time snapshot with no repeated observation of the same aircraft, so it can never form a valid pair. The result should be read as "beats dead reckoning on this simulator," not yet validated against real flight dynamics.

## Contextual anomaly detection (built on corridor discovery)

`ml/anomaly.py` scores each cruise point against its nearest corridor: lateral distance to centroid, heading deviation from modal heading, altitude z-score against that corridor's own members, plus a noise flag for unassigned points. Calibrated to a threshold of 0.62, currently flagging **3.64%** of scored points (target band 2-5%).

| bucket | count |
|---|---|
| rules_only | 226 |
| ml_only | 820 |
| both | 52 |
| neither | 22,874 |
| **total** | **23,972** |

820 states get flagged by the contextual model that rule thresholds structurally cannot see — that's the actual case for this model existing. (A sanity check — not the headline metric, since it's partly circular by construction — shows the ML model also flags 18.7% of the simulator's own rule-shaped injected anomalies.)

## Traffic forecast (gradient boosting, synthetic history)

`ml/forecast.py` predicts hourly flight counts from lag/seasonal features. **Labelled clearly, everywhere it appears in the API and dashboard, as trained on synthetic history** (`is_synthetic=True`) — real accumulated traffic history is only a couple of hours long, nowhere near enough for a `lag_24` feature, so a 7-day synthetic diurnal series stands in.

| | MAE | RMSE | MAPE |
|---|---|---|---|
| **model** | **8.506** | **10.814** | **0.1057** |
| baseline: last value | 12.759 | 14.835 | 0.1616 |
| baseline: same hour yesterday | 13.345 | 16.544 | 0.1496 |

Beats both naive baselines on every metric (test n=29 hours) — but the result describes the synthetic generator, not real traffic, until enough real hourly history accumulates to retrain against it.

## ML scoring in the cloud

See [aws-architecture.md](aws-architecture.md#ml-scoring-runs-in-the-cloud-too) for how corridor discovery and anomaly scoring are ported to the serverless AWS deployment (a reference-table export rather than a pickled model, since DBSCAN has no `.predict()`).
