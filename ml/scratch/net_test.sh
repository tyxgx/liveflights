#!/usr/bin/env bash
# Tiny network diagnosis: is the internet / GitHub reachable, and do small vs large byte-range
# requests to a known-good release file work? Each request has a 40 s timeout.
U="https://github.com/adsblol/globe_history_2026/releases/download/v2026.09.15-planes-readsb-prod-0/v2026.09.15-planes-readsb-prod-0.tar.aa"
echo "--- basic reachability"
curl -sS -m 15 -o /dev/null -w "example.com: HTTP %{http_code} in %{time_total}s\n" https://example.com || echo "example.com: FAILED rc=$?"
curl -sS -m 15 -o /dev/null -w "github.com:  HTTP %{http_code} in %{time_total}s\n" https://github.com || echo "github.com: FAILED rc=$?"
echo "--- byte-range requests to the 15 Sep release (same URL the extractor uses)"
for r in "0-511" "0-2097151" "0-8388607"; do
  curl -sSL -m 40 -r "$r" -o /dev/null \
    -w "range $r: HTTP %{http_code}, %{size_download} bytes, %{time_total}s, %{speed_download} B/s\n" "$U" \
    || echo "range $r: FAILED rc=$?"
done
echo "--- done"
