"""Survey the tar layout of EVERY planned day (all rounds) before downloading anything big.

Per day (read-only, only 256 KB pieces are downloaded, in parallel):
  * which asset parts exist (.aa/.ab/.ac...) and the total tar size (via a 1-byte range request),
  * a coarse map of the tar every --step-mb MB: T = traces/xx aircraft files, H = heatmap/,
    A = acas/, L = license, ? = unreadable piece (inside a big file), O = other,
  * where the extractor's find_trace_start() would begin, and whether the 1.3 GB it will stream
    from there is really all `traces/` (no heatmap in the middle),
  * a verdict per day: OK / WARN / MISSING.

Usage (from the repo root):
    python3 ml/scratch/survey_days.py                 # all rounds (about 50 days)
    python3 ml/scratch/survey_days.py --rounds 1      # only round 1
    python3 ml/scratch/survey_days.py --dates 2026.09.01,2025.10.15
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import re
import subprocess
import time

from extract_globe_day import BASE, PART_BYTES, ROUNDS, find_trace_start
from map_tar import curl_range, first_header

PARTS = ["aa", "ab", "ac", "ad", "ae"]
PIECE = 262_144


def asset_size(url: str) -> int | None:
    """Size of one asset part from a 1-byte range request (None if it does not exist)."""
    try:
        r = subprocess.run(["curl", "-sSL", "-r", "0-0", "-D", "-", "-o", "/dev/null", "-m", "90", url],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    m = re.findall(r"content-range:\s*bytes\s+0-0/(\d+)", r.stdout, flags=re.I)
    return int(m[-1]) if m else None


def piece(base: str, virt: int) -> str:
    """One-letter class of the first tar member found at a virtual tar offset."""
    idx = virt // PART_BYTES
    if idx >= len(PARTS):
        return "?"
    blk = curl_range(f"{base}.tar.{PARTS[idx]}", virt - idx * PART_BYTES, PIECE)
    fh = first_header(blk) if blk else None
    if not fh:
        return "?"
    name = fh[1]
    if "traces/" in name:
        return "T"
    if "heatmap" in name:
        return "H"
    if "acas" in name:
        return "A"
    if "LICENSE" in name:
        return "L"
    return "O"


def rle(seq: list[str]) -> str:
    """Run-length encode e.g. HHHTTT -> H3 T3."""
    out, i = [], 0
    while i < len(seq):
        j = i
        while j < len(seq) and seq[j] == seq[i]:
            j += 1
        out.append(f"{seq[i]}{j - i}")
        i = j
    return " ".join(out)


def survey_day(date: str, pod: str, step_mb: int, workers: int, quick: bool = False) -> dict:
    """Survey one day; returns a result dict (also printed by the caller)."""
    tag = f"v{date}-planes-readsb-{pod}"
    base = f"{BASE.format(year=date[:4])}/{tag}/{tag}"
    sizes = [asset_size(f"{base}.tar.{p}") for p in PARTS[:4]]
    if not sizes[0]:
        return {"date": date, "verdict": "MISSING", "note": f"no {pod} release / .aa part"}
    parts = [s for s in sizes if s]
    total = sum(parts)
    offs = [] if quick else [(o // 512) * 512 for o in range(0, total - PIECE, step_mb * 1_000_000)]
    with cf.ThreadPoolExecutor(workers) as ex:
        classes = list(ex.map(lambda o: piece(base, o), offs))
    start = find_trace_start(f"{base}.tar.aa")
    cont = []
    for k in ((600, 1200) if quick else (300, 600, 900, 1200)):
        o = start + k * 1_000_000
        if o < total - PIECE:
            cont.append((k, piece(base, (o // 512) * 512)))
    notes = []
    if start == 0:
        notes.append("TRACE START NOT FOUND (extractor would stream from byte 0)")
    bad = [k for k, c in cont if c == "H"]
    if bad:
        notes.append(f"heatmap inside the range at +{bad} MB")
    unclear = [k for k, c in cont if c == "?"]
    if unclear:
        notes.append(f"unreadable piece at +{unclear} MB")
    t_idx = [i for i, c in enumerate(classes) if c == "T"]
    avail = (max(t_idx) * step_mb - start / 1e6) if t_idx else 0
    if not quick and avail < 1100:
        notes.append(f"only ~{avail:.0f} MB of traces after the start")
    if len(sizes) > 1 and sizes[1] is None and total < 1_400_000_000:
        notes.append("tar smaller than the 1.3 GB we stream")
    verdict = "OK" if not notes else ("WARN")
    return {"date": date, "verdict": verdict, "sizes": [round(s / 1e6) for s in parts],
            "total": total / 1e6, "layout": rle(classes), "start": start / 1e6,
            "cont": "".join(c for _, c in cont), "note": "; ".join(notes)}


def main() -> None:
    """CLI."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates")
    ap.add_argument("--rounds", default="1,2,3,4")
    ap.add_argument("--pod", default="prod-0")
    ap.add_argument("--step-mb", type=int, default=400)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--quick", action="store_true",
                    help="skip the coarse map; only sizes, trace start and 2 continuity probes")
    ap.add_argument("--day-workers", type=int, default=2, help="how many days are surveyed at once")
    a = ap.parse_args()
    dates = a.dates.split(",") if a.dates else [d for r in a.rounds.split(",") for d in ROUNDS[int(r)]]
    print(f"surveying {len(dates)} days (pod {a.pod}, coarse map every {a.step_mb} MB)\n", flush=True)
    print(f"{'date':10} {'verdict':7} {'total MB':>8} {'parts MB':22} {'trace start MB':>14}  "
          f"{'+300/600/900/1200':17} layout (T=traces H=heatmap A=acas L=license ?=inside big file)",
          flush=True)
    results = []
    t0 = time.time()
    def run(d: str) -> dict:
        try:
            return survey_day(d, a.pod, a.step_mb, a.workers, a.quick)
        except Exception as exc:  # noqa: BLE001 - keep surveying the other days
            return {"date": d, "verdict": "ERROR", "note": f"{type(exc).__name__}: {exc}"}

    ex_days = cf.ThreadPoolExecutor(a.day_workers)  # a few days at a time (results stay in order)
    for r in ex_days.map(run, dates):
        d = r["date"]
        results.append(r)
        if r["verdict"] in ("MISSING", "ERROR"):
            print(f"{d:10} {r['verdict']:7} {r['note']}", flush=True)
        else:
            print(f"{d:10} {r['verdict']:7} {r['total']:>8,.0f} {str(r['sizes']):22} "
                  f"{r['start']:>14,.0f}  {r['cont']:17} {r['layout']}"
                  + (f"   !! {r['note']}" if r["note"] else ""), flush=True)
    ok = sum(r["verdict"] == "OK" for r in results)
    print(f"\nSUMMARY: {ok}/{len(results)} OK, "
          f"{sum(r['verdict'] == 'WARN' for r in results)} WARN, "
          f"{sum(r['verdict'] == 'MISSING' for r in results)} MISSING, "
          f"{sum(r['verdict'] == 'ERROR' for r in results)} ERROR | {(time.time() - t0) / 60:.1f} min")
    good = [r for r in results if "total" in r]
    if good:
        first_h = sum(1 for r in good if r["layout"].startswith(("A1 H", "L1 H", "O1 H")) or " H" in r["layout"][:8])
        print(f"total tar size range: {min(r['total'] for r in good):,.0f} - "
              f"{max(r['total'] for r in good):,.0f} MB; days with trace start > 200 MB: "
              f"{sum(r['start'] > 200 for r in good)}")


if __name__ == "__main__":
    main()
