#!/usr/bin/env bash
# Download the Virtual Radar Server "standing data" (CC0): routes, airports, airlines, aircraft,
# model types, registration prefixes, countries, code blocks. The whole repo is only ~50 MB.
# Also fetches adsb.lol's two ready-made bulk CSVs (routes.csv, airports.csv).
# Output: data/vrs/  (gitignored). Run from the repo root.
set -u
mkdir -p data/vrs
cd data/vrs || exit 1

echo "=== 1. git clone (or update) vradarserver/standing-data"
if [ -d standing-data/.git ]; then
  git -C standing-data pull --ff-only 2>&1 | tail -2
else
  git clone --depth=1 https://github.com/vradarserver/standing-data.git 2>&1 | tail -2
fi

echo
echo "=== 2. adsb.lol bulk CSVs (rebuilt every hour from the same data)"
curl -sSL -m 300 -o routes.csv   https://vrs-standing-data.adsb.lol/routes.csv   && echo "routes.csv OK"
curl -sSL -m 120 -o airports.csv https://vrs-standing-data.adsb.lol/airports.csv && echo "airports.csv OK"

echo
echo "=== 3. what we got (folder | files | size | first CSV: header + 2 sample rows)"
for d in standing-data/*/; do
  name=$(basename "$d")
  n=$(find "$d" -type f | wc -l | tr -d ' ')
  sz=$(du -sh "$d" | cut -f1)
  first=$(find "$d" -type f -name '*.csv' | sort | head -1)
  echo "--- $name | $n files | $sz | ${first:-no csv}"
  if [ -n "$first" ]; then head -3 "$first" | cut -c1-260; fi
done
echo
echo "=== 4. bulk file sizes and headers"
ls -la routes.csv airports.csv
head -3 routes.csv | cut -c1-260
echo "routes.csv rows: $(($(wc -l < routes.csv) - 1))   airports.csv rows: $(($(wc -l < airports.csv) - 1))"
echo
du -sh .
echo "--- done"
