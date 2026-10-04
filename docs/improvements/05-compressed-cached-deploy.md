# 05 · A repeatable, compressed and cached deploy (ships steps 03 to 05)

Status: **done and live (2026-10-04)**

## Situation

The site is a static Next.js export on S3. Two problems sat in the deploy path:

- The only recorded deploy command was a bare `aws s3 sync out/ ... --delete`. S3 website hosting does not compress and
  the objects had no `Cache-Control`, so every visit downloaded about 946 KB of JavaScript uncompressed and
  revalidated every file. Measured on the live bucket: `live.html` 15,826 bytes, no `Content-Encoding`, no cache header;
  the largest chunk was 456 KB raw.
- A plain `next build` bakes `.env.local` (the local API URL) into the export. This already shipped a broken live
  site once (2026-09-29). `build:prod` fixes the build, but nothing forced the deploy to use it.

Steps 03 and 04 (smooth map, polite polling) were also built but not yet visible to anyone.

## Task

One command that builds with the production environment, refuses to ship a build that points at localhost,
compresses text assets, sets sensible cache headers, uploads, and can be verified against the real URL. Then deploy
steps 03 to 05 and measure production, not just localhost.

## Action

- `web/scripts/deploy-prod.sh` (`pnpm deploy:prod`; `SKIP_BUILD=1` reuses `out/`):
  1. runs `build-prod.sh`;
  2. aborts if `out/_next/static` contains `localhost:8000`;
  3. gzips `.js .css .html .txt .json .svg` into a staging copy (names unchanged) and uploads them with
     `Content-Encoding: gzip`;
  4. headers: `_next/static` is `public, max-age=31536000, immutable` (file names are content hashes);
     HTML and RSC `.txt` are `no-cache` so a new deploy shows up at once; icons are one day.
  5. no `--delete` on `_next/`: older hashed files stay, so a page cached mid-deploy never points at a missing chunk.
- Ran it (`SKIP_BUILD=1`, using the build already tested in step 04), then checked the real URL.

## Result

Staged assets: **1,760 KB to 724 KB** (gzip). Live headers after the deploy:

| Object | Before | After |
|---|---|---|
| `live.html` | 15,826 B, no encoding, no cache header | 3,781 B, `gzip`, `no-cache` |
| a hashed JS chunk | raw | gzip, `immutable`, 1 year |

Production probe, headless Chrome on the real HTTPS site, 30 s (`data/step05-prod-cpu{1,4}.json`; baseline
`prod-recheck-*` was taken the same day before the deploy):

| | Before (prod) | After (prod) | CPU 4x before | CPU 4x after |
|---|---|---|---|---|
| JavaScript transferred | 946 KB | **259 KB** | 946 KB | **259 KB** |
| Total transferred in 30 s | 5,761 KB | 4,403 KB | 6,563 KB | 4,461 KB |
| Frames per second | 29.6 | **42.3** | 6.4 | **11.6** |
| Main thread blocked | 18.5 % | **6.8 %** | 90.4 % | **80.7 %** |
| Long tasks in 30 s | 52 | **19** | 260 | 221 |

`behavior-check.mjs` against production: all 6 checks pass (3,636 markers, gliding, click-to-focus, dimming, Escape).

**What did not improve, and why:** first aircraft on screen was 5.3 s before and 17.6 s after at CPU 1 (5.9 s and 23.1 s
at CPU 4), and LCP went from 4.3 s to 8.8 s. These are not caused by the deploy. The live-flights API averaged
4.9 to 5.1 s per call during the "after" runs against 1.0 to 1.3 s in the "before" runs; the first poll alone is a
1.6 MB uncompressed response through a throttled Lambda (the original baseline, taken at a slow API moment, also
showed 16 to 22 s). Rendering is now much cheaper, but the data path is the bottleneck. That is what steps 06 and 07
address (a static pre-compressed snapshot from S3, and a faster ingest).

**Caveats.**
- The probe shares a machine and network with whatever else is running, and the API varies run to run, so single
  runs differ; the main-thread and bytes figures are the reliable ones, the first-aircraft time is not.
- S3 cannot negotiate encodings: a client that does not send `Accept-Encoding: gzip` would still receive gzip. Every
  current browser sends it, so this is accepted for a static demo site.
- 80.7 % blocked at CPU 4x is still high: the 3,600 DOM markers are the remaining cost (step 08).

**Rollback:** re-run the old `aws s3 sync out/ s3://liveflights-prod-site-922120357133/ --delete` from the previous
commit's build, or `git revert` and `pnpm deploy:prod`.
