"""EXACT structure audit of one full day of a globe_history tarball (streams the whole tar once).

It downloads the complete day (~4 GB, about 10-15 minutes) but keeps nothing on disk. It reports:
  * every top-level section of the tar (acas/, heatmap/, traces/, licenses ...): number of files,
    total MB, and the byte range where it sits (so you see the exact layout of that day),
  * inside traces/: number of hex folders, files and MB per folder, how many are `~` (non-ICAO)
    addresses, any file name that does NOT look like trace_full_<hex>.json, file-size percentiles,
  * for every --sample-every-th aircraft file: which JSON keys exist and how often, trace point
    lengths, which keys appear inside the per-point aircraft objects,
  * the share of aircraft that touch the Europe box: whole day vs the first 1 GB of trace data
    (this checks whether "the first 1 GB" is an unbiased sample of the day).

Usage (from the repo root):
    python3 ml/scratch/audit_day.py --date 2026.09.15
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import subprocess
import tarfile
import time

import numpy as np
from extract_globe_day import BASE, LAT0, LAT1, LON0, LON1

PARTS = ["aa", "ab", "ac", "ad", "ae"]
NAME_OK = re.compile(r"^trace_full_(~?)([0-9a-f]{6})\.json$")


class StreamAll:
    """One stream over the whole tar: assets .aa, .ab, .ac ... chained (each fully downloaded)."""

    def __init__(self, base: str) -> None:
        self.base, self.i, self.proc, self.n = base, 0, None, 0
        self._open()

    def _close(self) -> None:
        if self.proc is not None:
            self.proc.stdout.close()
            self.proc.terminate()
            self.proc.wait()
            self.proc = None

    def _open(self) -> bool:
        self._close()
        if self.i >= len(PARTS):
            return False
        url = f"{self.base}.tar.{PARTS[self.i]}"
        self.i += 1
        self.proc = subprocess.Popen(["curl", "-sSfL", url], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        return True

    def read(self, size: int = -1) -> bytes:
        """Read up to size bytes, moving to the next part at the end of one."""
        while self.proc is not None:
            b = self.proc.stdout.read(size)
            if b:
                self.n += len(b)
                return b
            if not self._open():
                break
        return b""


def touches_europe(tr: list) -> bool:
    """True if any trace point lies inside the Europe box."""
    for p in tr:
        if len(p) > 2 and isinstance(p[1], (int, float)) and isinstance(p[2], (int, float)):
            if LAT0 <= p[1] <= LAT1 and LON0 <= p[2] <= LON1:
                return True
    return False


def pct(a: list[float], q: float) -> float:
    """Percentile helper that tolerates an empty list."""
    return float(np.percentile(a, q)) if a else float("nan")


def main() -> None:
    """Run the audit."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026.09.15")
    ap.add_argument("--pod", default="prod-0")
    ap.add_argument("--sample-every", type=int, default=4, help="decode every N-th aircraft file")
    a = ap.parse_args()
    tag = f"v{a.date}-planes-readsb-{a.pod}"
    base = f"{BASE.format(year=a.date[:4])}/{tag}/{tag}"
    t0 = time.time()

    sections: dict[str, list[float]] = collections.defaultdict(lambda: [0, 0, None, 0])
    trace_dirs: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    sizes: list[int] = []
    odd: list[str] = []
    tilde = 0
    n_trace = 0
    trace_bytes = 0
    keys: collections.Counter = collections.Counter()
    ptlen: collections.Counter = collections.Counter()
    objkeys: collections.Counter = collections.Counter()
    n_obj_pts = n_pts = 0
    n_dec = n_dec_err = 0
    eu = {"first_gb": [0, 0], "rest": [0, 0]}  # [decoded, touching Europe]
    last_print = 0

    src = StreamAll(base)
    with tarfile.open(fileobj=src, mode="r|") as tf:
        for m in tf:
            parts = [p for p in m.name.split("/") if p not in ("", ".")]
            if not m.isfile():
                continue
            top = parts[0] if len(parts) > 1 else f"(file) {parts[0]}"
            s = sections[top]
            s[0] += 1
            s[1] += m.size
            s[2] = m.offset if s[2] is None else s[2]
            s[3] = m.offset + m.size
            if top == "traces" and len(parts) >= 3:
                fn = parts[-1]
                trace_dirs[parts[1]][0] += 1
                trace_dirs[parts[1]][1] += m.size
                sizes.append(m.size)
                n_trace += 1
                trace_bytes += m.size
                mm = NAME_OK.match(fn)
                if not mm:
                    if len(odd) < 30:
                        odd.append(m.name)
                elif mm.group(1) == "~":
                    tilde += 1
                elif n_trace % a.sample_every == 0:
                    raw = tf.extractfile(m).read()
                    try:
                        d = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
                    except (OSError, ValueError):
                        n_dec_err += 1
                        continue
                    n_dec += 1
                    keys.update(d.keys())
                    tr = d.get("trace") or []
                    for p in tr[:50]:
                        ptlen[len(p)] += 1
                    for p in tr:
                        n_pts += 1
                        if len(p) > 8 and isinstance(p[8], dict):
                            n_obj_pts += 1
                            objkeys.update(p[8].keys())
                    bucket = "first_gb" if trace_bytes <= 1_000_000_000 else "rest"
                    eu[bucket][0] += 1
                    eu[bucket][1] += int(touches_europe(tr))
            if src.n - last_print >= 500_000_000:
                last_print = src.n
                print(f"  ... {src.n / 1e9:.1f} GB read, {n_trace:,} trace files, "
                      f"{time.time() - t0:.0f}s", flush=True)

    print(f"\n=========== AUDIT {a.date} ({a.pod}) | streamed {src.n / 1e6:,.0f} MB in "
          f"{(time.time() - t0) / 60:.1f} min ===========")
    print("\n1) SECTIONS of the tar (bytes are the position inside the whole tar):")
    print(f"   {'section':22} {'files':>8} {'MB':>9}  {'first MB':>9} -> {'last MB':>9}")
    for name, (f, b, o1, o2) in sorted(sections.items(), key=lambda kv: kv[1][2] or 0):
        print(f"   {name:22} {f:>8,.0f} {b / 1e6:>9,.0f}  {o1 / 1e6:>9,.0f} -> {o2 / 1e6:>9,.0f}")

    print("\n2) traces/ in detail:")
    nd = len(trace_dirs)
    fpd = [v[0] for v in trace_dirs.values()]
    mpd = [v[1] / 1e6 for v in trace_dirs.values()]
    print(f"   hex folders: {nd} | files per folder min/median/max = {min(fpd, default=0)}/"
          f"{pct(fpd, 50):.0f}/{max(fpd, default=0)} | MB per folder min/median/max = "
          f"{min(mpd, default=0):.1f}/{pct(mpd, 50):.1f}/{max(mpd, default=0):.1f}")
    print(f"   trace files: {n_trace:,} total, {tilde:,} are '~' (non-ICAO) = "
          f"{tilde / max(n_trace, 1):.0%}; usable ICAO aircraft: {n_trace - tilde - len(odd):,}")
    print(f"   file size (gzip, KB): p50={pct(sizes, 50) / 1e3:.1f} p90={pct(sizes, 90) / 1e3:.1f} "
          f"p99={pct(sizes, 99) / 1e3:.1f} max={max(sizes, default=0) / 1e3:.0f}")
    print(f"   names NOT matching trace_full_<hex>.json: {len(odd)} {odd[:8]}")

    print(f"\n3) INSIDE the aircraft files (decoded {n_dec:,}, errors {n_dec_err}):")
    print("   top-level keys (share of files): " + ", ".join(
        f"{k}={v / max(n_dec, 1):.0%}" for k, v in keys.most_common()))
    print(f"   trace point lengths (first 50 points per file): {dict(ptlen.most_common(6))}")
    print(f"   points carrying an aircraft object: {n_obj_pts / max(n_pts, 1):.0%} of {n_pts:,}")
    print("   keys inside those objects (share of objects): " + ", ".join(
        f"{k}={v / max(n_obj_pts, 1):.0%}" for k, v in objkeys.most_common(45)))

    print("\n4) Is the first 1 GB of trace data an unbiased sample? (aircraft touching Europe)")
    for k, (dec, e) in eu.items():
        print(f"   {k:9}: decoded {dec:,}, touching Europe {e:,} = {e / max(dec, 1):.1%}")
    tot_dec = sum(v[0] for v in eu.values())
    tot_e = sum(v[1] for v in eu.values())
    print(f"   whole day: {tot_e / max(tot_dec, 1):.1%} -> about "
          f"{tot_e / max(tot_dec, 1) * (n_trace - tilde):,.0f} Europe aircraft that day")


if __name__ == "__main__":
    main()
