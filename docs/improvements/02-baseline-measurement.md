# 02. Baseline performance measurement (2026-10-04)

## Situation
The live dashboard "glitches a little and feels slow". That is a feeling, and a feeling cannot be fixed or proven fixed. The audit of the code and the API (see
[01](01-aws-cost-audit.md)) gave strong suspects: a 500 ms timer that rebuilds the state of about 3,600 aircraft, uncompressed 1.6 MB API responses polled every
15 seconds, thousands of DOM markers, and Lambda throttling. None of them had been measured in a real browser.

## Task
A repeatable measurement that turns "slow and glitchy" into numbers: how long until aircraft appear, how much of the time the main thread is blocked, the frame
rate, the bytes and the failures. It must run the same way before and after every change.

## Action
- Wrote `web/perf/probe.mjs`, a small probe that drives the installed Chrome in headless mode (puppeteer-core, no browser download, no access to any personal
  browser profile). It opens the dashboard, waits for the aircraft, then observes a 30 second steady-state window.
- It records: First and Largest Contentful Paint, layout shift, time until the first 100 aircraft are on the map, long tasks (main-thread blocks over 50 ms, via
  `PerformanceObserver`), frames per second (counted with `requestAnimationFrame`), JS heap, DOM node count, and every network request with its real wire size,
  duration, compression and status (via the Chrome DevTools protocol).
- Ran it against production twice: at normal CPU speed and at 4x slower (`Emulation.setCPUThrottlingRate`), which approximates a mid-range laptop or a phone.
- Raw results are kept in `docs/improvements/data/` so later steps compare against exactly these files.

```
cd web/perf && npm install
node probe.mjs --label baseline --cpu 1 --seconds 30 --out ../../docs/improvements/data/NAME.json
node probe.mjs --url http://localhost:3000/live --cpu 4          # against a local build
```

## Result
Production, 2026-10-04, 30 second window:

| Measure | CPU 1x | CPU 4x (mid-range device) |
|---|---|---|
| First 100 aircraft visible after | **16.4 s** | **22.0 s** |
| First / Largest Contentful Paint | 1.9 s / 4.2 s | 1.5 s / 3.6 s |
| Cumulative layout shift | 0.001 | 0 |
| Aircraft markers / DOM nodes | 3,576 / 15,264 | 3,552 / 15,168 |
| Long tasks in 30 s | 70 | **261** |
| Main thread blocked | 5.8 s (**19%**) | 28.6 s (**93.8%**) |
| Worst single block | 484 ms | 481 ms |
| Frame rate | **31.6 fps** | **5.5 fps** |
| JS heap at start, then 30 s later | 19 MB, 72 MB | 46 MB, 92 MB |
| Network | 6.6 MB in 53 requests (JS 946 KB, map tiles 98 KB, **API 5.4 MB**) | the same |
| API calls / average / slowest | 21 / **5.0 s** / **20.6 s** | 24 / 1.7 s / 6.0 s |
| API responses with no compression | **21 of 21** | 23 of 24 |
| Failed requests | none | **one 503** on `/api/flights/live?limit=6000` |

What this confirms
1. **The glitch is real and it is a main-thread problem.** At 1x the page runs at 32 fps with 19% of the time blocked; on a 4x slower CPU it is a slideshow
   (5.5 fps, blocked 94% of the time). Aircraft take 16 to 22 seconds to appear.
2. **The API is the biggest cost of loading:** 5.4 MB of the 6.6 MB, none of it compressed, with calls averaging 5 seconds.
3. **Failures are visible to users:** a 503 on the main data call was captured in a single 30 second run, matching the 202 Lambda throttles found in the cost audit.
4. **Layout shift is already fine** (0.001), so it is not worth touching. Map tiles are small (98 KB).
5. Heap growth from 19 MB to 72 MB in 30 seconds is not proof of a leak (it includes the first large data parse), but it is tracked in every later run.

Limits of these numbers: one machine, one network (the API is in us-east-1 and the test ran from India), headless Chrome with software rendering, and a
single run each. They are for before/after comparison on the same setup, not absolute claims about real users.

## What this led to
It sets the targets for steps 03 to 08. Success is judged against this table: long tasks, blocked time, fps, time to first aircraft, API bytes and zero failures.
