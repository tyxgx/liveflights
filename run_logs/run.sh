#!/usr/bin/env bash
# Run a command from the liveflights repo root and save the command + full output
# into run_logs/ (one .log file per run + one line in run_logs/INDEX.txt).
#
# Usage:  bash run_logs/run.sh <short_name> "<command>"
# Example: bash run_logs/run.sh gru_smoke "python3 ml/scratch/build_gru_windows.py --smoke"
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
NAME="${1:?usage: run.sh <short_name> \"<command>\"}"
CMD="${2:?usage: run.sh <short_name> \"<command>\"}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG="$HERE/${STAMP}_${NAME}.log"

cd "$ROOT" || exit 1

# CURRENT.log always points to the log of the run that is happening right now (or the last one),
# so the live progress of a running command can be read without knowing its file name.
ln -sf "$LOG" "$HERE/CURRENT.log"

{
  echo "# name: $NAME"
  echo "# started: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# cwd: $ROOT"
  echo "# command: $CMD"
  echo "# ------------------------------------------------------------"
} | tee "$LOG"

START=$(date +%s)
bash -c "$CMD" 2>&1 | tee -a "$LOG"
CODE=${PIPESTATUS[0]}
SECS=$(( $(date +%s) - START ))

{
  echo "# ------------------------------------------------------------"
  echo "# finished: $(date '+%Y-%m-%d %H:%M:%S') | exit code: $CODE | seconds: $SECS"
} | tee -a "$LOG"

echo "$STAMP | $NAME | exit=$CODE | ${SECS}s | $CMD | $(basename "$LOG")" >> "$HERE/INDEX.txt"

# LATEST.txt = a SHORT, capped copy of this run (fast to read): progress lines split, long lines
# cut to 400 chars, and if longer than 200 lines only the first 60 + last 140 are kept.
{
  echo "# $STAMP | $NAME | exit=$CODE | ${SECS}s | $CMD"
  echo "# full log: $(basename "$LOG")"
  tr '\r' '\n' < "$LOG" | cut -c1-400 | awk -v H=60 -v T=140 '
    { a[NR] = $0 }
    END {
      if (NR <= H + T) { for (i = 1; i <= NR; i++) print a[i] }
      else {
        for (i = 1; i <= H; i++) print a[i]
        print "... [" NR - H - T " lines omitted, see the full log] ..."
        for (i = NR - T + 1; i <= NR; i++) print a[i]
      }
    }'
} > "$HERE/LATEST.txt"
