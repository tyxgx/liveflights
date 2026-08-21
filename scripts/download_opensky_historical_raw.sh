#!/usr/bin/env bash
# Downloads raw OpenSky historical sample-archive files (compressed CSVs,
# untouched) for the given Mondays into data/opensky_historical_raw/<date>/.
# No processing/mapping/Spark here — just the download, for loading with
# your own PySpark script later.
#
# Usage:
#   scripts/download_opensky_historical_raw.sh 2022-03-07 2022-03-28 2022-04-18 2022-05-16

set -euo pipefail

DATES=("$@")
if [ ${#DATES[@]} -eq 0 ]; then
  DATES=("2022-03-07" "2022-03-28" "2022-04-18" "2022-05-16")
fi

OUT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/opensky_historical_raw"
BASE_URL="https://s3.opensky-network.org/data-samples/states"

for date in "${DATES[@]}"; do
  mkdir -p "$OUT_ROOT/$date"
  for hour in $(seq -w 0 23); do
    out_file="$OUT_ROOT/$date/states_${date}-${hour}.csv.gz"
    if [ -f "$out_file" ]; then
      echo "skip (already downloaded): $date $hour"
      continue
    fi

    tmp_tar="$OUT_ROOT/$date/.tmp_${hour}.tar"
    url="${BASE_URL}/${date}/${hour}/states_${date}-${hour}.csv.tar"
    # --max-time caps the whole request (not just idle time) — a stalled
    # connection was hanging for hours with no error and no timeout,
    # requiring manual detection + kill. 180s is generous for a ~137MB file
    # even on a slow connection; retry once before giving up on the hour.
    ok=0
    for attempt in 1 2; do
      if curl -sf --max-time 180 -o "$tmp_tar" "$url"; then
        ok=1
        break
      fi
      echo "attempt $attempt failed: $date $hour"
      rm -f "$tmp_tar"
    done
    if [ "$ok" -eq 0 ]; then
      echo "FAILED download: $date $hour"
      continue
    fi

    tar -xf "$tmp_tar" -C "$OUT_ROOT/$date"
    rm -f "$tmp_tar"

    free_gb=$(df -g . | awk 'NR==2 {print $4}')
    size=$(du -sh "$out_file" 2>/dev/null | cut -f1)
    echo "$date $hour: downloaded ($size, free disk: ${free_gb}GB)"

    if [ "$free_gb" -lt 5 ]; then
      echo "STOPPING: free disk below 5GB safety margin"
      exit 1
    fi
  done
done

echo "Done. Total size:"
du -sh "$OUT_ROOT"
