#!/usr/bin/env bash
# Is it your internet in general, or GitHub's release downloads specifically?
# Downloads ~10 MB from Cloudflare (a fast global CDN), then 4 MB from the GitHub release.
# Each has a 40 s timeout.
echo "--- Cloudflare speed test (10 MB)"
curl -sS -m 40 -o /dev/null \
  -w "cloudflare: HTTP %{http_code}, %{size_download} bytes, %{time_total}s, %{speed_download} B/s\n" \
  "https://speed.cloudflare.com/__down?bytes=10000000" || echo "cloudflare: FAILED rc=$?"
echo "--- GitHub release (4 MB range of the 15 Sep file)"
U="https://github.com/adsblol/globe_history_2026/releases/download/v2026.09.15-planes-readsb-prod-0/v2026.09.15-planes-readsb-prod-0.tar.aa"
curl -sSL -m 40 -r 0-4194303 -o /dev/null \
  -w "github:     HTTP %{http_code}, %{size_download} bytes, %{time_total}s (first byte after %{time_starttransfer}s), %{speed_download} B/s\n" "$U" \
  || echo "github: FAILED rc=$?"
echo "--- done"
