# 03. Stop the 500 ms full-page re-render (2026-10-04)

## Situation
[Step 02](02-baseline-measurement.md) measured the dashboard: at normal CPU speed the main thread was blocked 19% of the time at 30 fps; on a 4x slower CPU it was
blocked 91 to 94% of the time at 5 to 6 fps. Reading the code showed why. `useFlightsPolling` ran a timer every 500 ms that mapped over about 3,600 aircraft, built a
new object for each one and called `setFlights()`. That put a new array into React state twice a second, so the whole live page (top bar, panels, charts, map)
re-rendered twice a second, and `AircraftLayer` then walked all 3,600 markers and rewrote about 15 DOM properties on each one. All of that just to move each plane
a few metres.

## Task
Keep the planes gliding smoothly between the 15-second polls, but make the cost of that motion almost nothing: no React state churn, and no DOM writes for anything
that did not change. Selecting an aircraft, dimming the others and everything else on the map must behave exactly as before.

## Action
1. **Moved the dead-reckoning out of React.** `projectPosition` (great-circle forward projection) moved to `lib/geo.ts` unchanged. `useFlightsPolling` no longer has a
   timer: `flights` changes only when a poll lands. (About 80 lines of the hook went away.)
2. **Split `AircraftLayer` into three effects, each running only when its own inputs change:**
   - *A, per poll:* create or remove markers and snap them to the polled position.
   - *B, on poll, selection or anomaly change:* colour, rotation, focus outline and dimming, written **only if different** from what the marker already shows
     (a small per-aircraft cache of the last applied appearance).
   - *C, glide:* once a second, from a `requestAnimationFrame` loop, dead-reckon each aircraft from its polled position and move its marker. Aircraft outside the visible
     map (plus a margin) are skipped and catch up on the next poll; nothing runs while the tab is hidden. At the default zoom a plane moves well under a pixel a second, so
     1 Hz looks continuous.
3. **Fixed a trap found on the way.** `FlightMap` built the filtered aircraft list with a plain `.filter()` on every render (when "anomalies only" is on), which would have looked like
   a new poll to the layer and snapped every plane back to its stale position. It is memoised now.
4. **Wired the new `positionsAsOf` prop** from the page through `FlightMap` to the layer. In local WebSocket mode the positions are already live, so it is `null` and nothing glides.
5. **Alternatives considered:** CSS transitions between updates (rejected: they fight Leaflet's own zoom animation), moving the whole thing to canvas or WebGL (the right
   long-term answer, kept for step 08 because it is a bigger change), and simply slowing the timer to 2 s (rejected: it keeps the re-render, just less often).
6. **Wrote a behaviour check** (`web/perf/behavior-check.mjs`) so the change cannot silently break the map: markers appear, they glide, clicking selects exactly one and dims the
   rest, Escape clears it, and no uncaught page errors.

## Result
Same machine, same hour, same 30 second window, paired runs (the baseline was run twice because API speed varies between runs; the window numbers below do not depend on it):

| | Baseline (two runs) | After step 03 |
|---|---|---|
| CPU 1x: long tasks in 30 s | 70, 52 | **26** |
| CPU 1x: main thread blocked | 19%, 18.5% | **7.7%** |
| CPU 1x: frame rate | 31.6, 29.6 fps | **39 fps** |
| CPU 1x: worst single block | 484, 941 ms | 376 ms |
| CPU 4x: long tasks | 261, 260 | **191** |
| CPU 4x: main thread blocked | 93.8%, 90.4% | **65.8%** |
| CPU 4x: frame rate | 5.5, 6.4 fps | **15.2 fps** |

Behaviour check: all 6 checks pass (3,584 markers; 37 of 200 sampled markers moved visibly in 4 s; click selects one and dims 3,583 others; Escape clears both).

What is better: React no longer re-renders the dashboard twice a second, and the map does a fraction of the DOM work. What is **not** fixed yet, honestly: 39 fps at normal speed and 15 fps
on a slow device are still not smooth. The remaining blocks come from each 15-second poll (parsing a 1.6 MB uncompressed response, re-rendering the panels, updating 3,600 markers)
and from having 3,600 DOM nodes at all. Those are steps 04 to 08. The "time until the first aircraft appears" numbers in the table of step 02 swing with API speed (1 to 5 s average per call) and
are not claimed as an effect of this step.

Typecheck is clean (`tsc --noEmit`). The repository has no ESLint configuration (`next lint` asks to create one), so linting the web code is an open item.

**Not deployed yet.** The change is built and tested locally (`pnpm build:prod`, served on localhost:3000, which is an origin the API's CORS allows). It ships with the compressed deploy of step 05.

How to verify: `cd web && pnpm build:prod && python3 -m http.server 3000 -d out`, then `node perf/probe.mjs --url http://localhost:3000/live.html --cpu 4` and `node perf/behavior-check.mjs`.
Rollback: `git revert` the commit; no data or infrastructure is involved.
