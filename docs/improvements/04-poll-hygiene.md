# 04 · Poll hygiene: no stacked requests, no polling for hidden tabs

Status: **done (code, tested locally, not yet deployed — ships with step 05)**

## Situation

The live map fetches `/api/flights/live?limit=6000` every 15 s (about 1.6 MB uncompressed). Three things in the
polling code made a slow or throttled API worse:

- **No overlap guard.** `setInterval(poll, 15000)` fires whether or not the previous request has finished. The
  Lambda is throttled (account concurrency limit 10, throttles on 09-28, 09-29 and 10-02; see
  [01-aws-cost-audit.md](01-aws-cost-audit.md)), so when one call is slow the browser stacks a second and third on
  top. That adds load exactly when the backend is struggling, and the responses can arrive out of order.
- **No cancel and no timeout.** A request that hangs stays "in flight" for as long as the browser allows, and
  leaving the page does not stop it.
- **Polls while nobody is looking.** A background tab keeps downloading 1.6 MB every 15 s. This is wasted
  Lambda invocations and wasted data, and the tab does a full update when the user returns.

`usePolledData` (used by the KPI, anomaly, country and forecast panels) had the same overlap problem, and it also
called `setLoading(true)` on every background refresh, which re-rendered every panel on every interval.

## Task

Make polling polite and safe without changing what the user sees: never more than one live-flights request in
flight, cancel on unmount, stop polling while the tab is hidden, and refresh at once when a stale tab comes back.
Prove it with a test that fails on the old code.

## Action

- `web/lib/api.ts`: `get()` takes an optional `AbortSignal` and applies a 25 s ceiling per request (previously
  none). A deliberate cancel is rethrown as-is so callers can tell it from an API failure; `liveFlights(limit,
  signal)` passes the signal through.
- `web/hooks/useFlightsPolling.ts`: an `inFlight` flag skips a tick if the previous poll is still running; one
  `AbortController` is aborted on unmount; ticks are skipped while `document.hidden`; a `visibilitychange`
  listener polls immediately when the tab becomes visible and the last good response is 15 s old or more. A
  cancelled request no longer sets the status to "reconnecting".
- `web/hooks/usePolledData.ts`: same in-flight guard and hidden-tab pause; background refreshes no longer toggle
  `loading` (only the first load and an explicit `refetch()` do); results arriving after unmount are ignored.
- `web/perf/poll-hygiene-check.mjs`: new regression check. It delays the live-flights call by 20 s (more than half
  the poll interval, so an unguarded poller must overlap itself), then hides the tab and counts requests, then
  shows it again.

## Result

Same test, old code vs new code (raw output in `docs/improvements/data/step04-hygiene-{before,after}.txt`):

| Check (38 s with a 20 s API, then 35 s hidden) | Before | After |
|---|---|---|
| Live-flights requests started in 38 s | 3 | **1** |
| Max requests in flight at once | 3 | **1** |
| Requests while the tab is hidden (35 s) | 3 | **0** |
| Stale tab becomes visible again | n/a (it never stopped) | **1 request within 1.5 s** |
| Uncaught page errors | 0 | 0 |

- `node perf/behavior-check.mjs`: all 6 checks still pass (3,482 markers, gliding, click-to-focus, dimming, Escape).
- `npx tsc --noEmit`: clean.
- **Frame rate did not change, and this step does not claim it would.** The step 04 probe runs (30 s, prod API)
  were dominated by API behaviour, not by rendering: one run saw a 503 and a 19.5 s slowest call, with the first
  aircraft at 21 s, because the live API was throttling at that moment. They are kept in `data/step04-cpu{1,4}.json`
  for completeness but are not a before/after comparison. Rendering cost is addressed in steps 07 and 09.
- Not covered: the "before" run of the third check is not meaningful (the old code never stopped polling), and the
  test was run in headless Chrome, not on a phone.

**Rollback:** `git revert` this commit; the three files are self-contained.

## What this changes for the next steps

A throttled API now gets at most one request per tab at a time and none from hidden tabs, which lowers the chance of
the 503s seen in the probe. Step 05 deploys steps 03 to 05 with compression and caching.
