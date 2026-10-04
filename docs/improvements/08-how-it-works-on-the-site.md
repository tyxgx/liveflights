# 08 · "How it works" on the landing page and the dashboard

Status: **done and live (2026-10-04)**

## Situation

The landing page had a five-card "Pipeline" section written before the prediction model and the S3 snapshot existed. It said the API
read "two small S3 objects", never mentioned the model, and never said the data comes from volunteer receivers or that coverage is
incomplete. A visitor who went straight to the live map had no pointer to any explanation.

## Task

Say, in plain words and in one place, where the data comes from, how it is processed and how it is shown, matching what is deployed
today, and make it reachable from both the landing page and the live dashboard. The page demonstrates a system; it must not read like a sales page.

## Action

- `web/components/landing/Pipeline.tsx` rewritten as three blocks: (1) adsb.lol, eight points, real but incomplete coverage;
  (2) the minute Lambda, the small compressed map file, the raw archive, the GRU model scored against reality; (3) static page, 15 s
  fetch, movement between updates, click for details. A one-line limits note: Europe only, volunteer coverage, predictions are an
  experiment with live accuracy.
- Navigation: "How it works" link on the landing page nav and in the live dashboard's top bar (`/index.html#pipeline`).
- Nothing else on the landing page was removed.

## Result

- Live (headless Chrome on the production URL): the three headings render, the nav link is present on the landing page, the dashboard
  top-bar link is present, 3,740 aircraft still render, no page errors; no horizontal overflow at 390 px.
- The text describes only deployed things; "about 140 KB" is the measured size of `live/map.json` (141,765 bytes on 2026-10-04).
- Not covered: the text is hand-written, and the model accuracy is not repeated in it (the dashboard shows the live figure instead).

**Rollback:** `git revert` and `pnpm deploy:prod`.
