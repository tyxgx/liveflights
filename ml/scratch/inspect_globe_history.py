"""Inspect a SMALL SAMPLE of adsb.lol's public `globe_history_2026` GitHub release.

Nothing is written outside data/globe_history_sample/ (gitignored). It downloads a few
small byte-ranges (default ~64 MB in total, NOT the 3.9 GB files) and answers:

  1. Are the `prod-0` and `staging-0` releases duplicates? (byte-identical ranges? same aircraft?)
  2. What does `mlatonly-0` contain?
  3. How dense are the trace points in Europe (time gap between points)?
  4. How often are the optional fields present (roll, ias, track_rate, nav_altitude_mcp, wind ...)?
  5. Your real download speed from GitHub.

Usage (from the repo root):
    python3 ml/scratch/inspect_globe_history.py            # quick, ~64 MB
    python3 ml/scratch/inspect_globe_history.py --full     # bigger sample, ~250 MB
    python3 ml/scratch/inspect_globe_history.py --day 2026.09.21
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import subprocess
import time

import numpy as np

BASE = "https://github.com/adsblol/globe_history_2026/releases/download"
OUT_DIR = "data/globe_history_sample"
PART_BYTES = 2_000_000_000  # size of the .tar.aa part (the tar is split at 2 GB)
LAT0, LAT1, LON0, LON1 = 35.0, 64.0, -13.0, 33.0  # Europe box (same as the live pipeline)
QUICK_MB = [0, 900, 2500]  # positions (MB) inside the ~3.9 GB tar: start, late .aa, inside .ab
FULL_MB = [0, 400, 800, 1200, 1600, 1950, 2200, 2800, 3400]
MLAT_MB = {"quick": [0, 150], "full": [0, 100, 200, 300]}
COLS = {
    3: "alt_baro", 4: "gs", 5: "track", 7: "baro_rate", 8: "aircraft_obj",
    9: "source_type", 10: "alt_geom", 11: "geom_rate", 12: "ias", 13: "roll",
}


def in_europe(p: list) -> bool:
    """True if a trace point (list) lies inside the Europe box."""
    return (
        len(p) > 2 and isinstance(p[1], (int, float)) and isinstance(p[2], (int, float))
        and LAT0 <= p[1] <= LAT1 and LON0 <= p[2] <= LON1
    )


def fetch(url: str, start: int, length: int, path: str) -> tuple[bool, str]:
    """Download bytes [start, start+length) with curl. Returns (ok, message)."""
    if os.path.exists(path) and os.path.getsize(path) == length:
        return True, "cached"
    cmd = [
        "curl", "-sSL", "-r", f"{start}-{start + length - 1}",
        "--max-filesize", str(length + 1_000_000), "-m", "900",
        "-o", path, "-w", "%{http_code} %{speed_download}", url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out.startswith("206"):
        return False, f"curl rc={r.returncode} out={out!r} err={r.stderr.strip()[:200]}"
    code, speed = out.split()
    return True, f"HTTP {code}, {float(speed) / 1e6:.2f} MB/s"


def parse_tar_chunk(data: bytes) -> list[tuple[str, bytes]]:
    """Parse whole tar members from an arbitrary 512-aligned slice of a tar."""
    n, i = len(data), 0
    while i + 512 <= n and data[i + 257:i + 262] != b"ustar":
        i += 512
    members: list[tuple[str, bytes]] = []
    next_name: str | None = None
    while i + 512 <= n:
        h = data[i:i + 512]
        if h == b"\0" * 512:
            break
        try:
            size = int(h[124:136].split(b"\0")[0].strip() or b"0", 8)
        except ValueError:
            break
        typ = h[156:157]
        body_start = i + 512
        body_end = body_start + size
        if body_end > n:
            break
        body = data[body_start:body_end]
        i = body_start + ((size + 511) // 512) * 512
        if typ == b"L":
            next_name = body.split(b"\0")[0].decode("utf8", "replace")
        elif typ in (b"x", b"g"):
            for line in body.split(b"\n"):
                if b" path=" in line:
                    next_name = line.split(b" path=", 1)[1].decode("utf8", "replace")
        elif typ in (b"0", b"\0"):
            name = h[:100].split(b"\0")[0].decode("utf8", "replace")
            prefix = h[345:500].split(b"\0")[0].decode("utf8", "replace")
            members.append((next_name or (f"{prefix}/{name}" if prefix else name), body))
            next_name = None
    return members


class Stats:
    """Accumulates coverage statistics over Europe trace points."""

    def __init__(self) -> None:
        self.files = self.parse_err = self.eu_aircraft = self.eu_points = 0
        self.gaps: list[float] = []
        self.pts_per_ac: list[int] = []
        self.col: collections.Counter = collections.Counter()
        self.src: collections.Counter = collections.Counter()
        self.obj_key: collections.Counter = collections.Counter()
        self.ac_has_key: collections.Counter = collections.Counter()
        self.top_keys: collections.Counter = collections.Counter()
        self.type_codes: collections.Counter = collections.Counter()
        self.ground = self.stale = self.newleg = 0
        self.example: dict | None = None

    def add_file(self, raw: bytes) -> None:
        """Parse one per-aircraft file (gzip JSON) and update the counters."""
        self.files += 1
        try:
            d = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        except (OSError, ValueError):
            self.parse_err += 1
            return
        self.top_keys.update(d.keys())
        tr = d.get("trace") or []
        eu = [p for p in tr if in_europe(p)]
        if not eu:
            return
        self.eu_aircraft += 1
        self.eu_points += len(eu)
        self.pts_per_ac.append(len(eu))
        if d.get("t"):
            self.type_codes[d["t"]] += 1
        for prev, cur in zip(tr, tr[1:], strict=False):
            if in_europe(prev) and in_europe(cur) and len(self.gaps) < 2_000_000:
                self.gaps.append(cur[0] - prev[0])
        keys_seen: set[str] = set()
        for p in eu:
            for idx in COLS:
                if len(p) > idx and p[idx] is not None:
                    self.col[idx] += 1
            if len(p) > 3 and p[3] == "ground":
                self.ground += 1
            if len(p) > 9 and p[9] is not None:
                self.src[str(p[9])] += 1
            if len(p) > 6 and isinstance(p[6], int):
                self.stale += p[6] & 1
                self.newleg += (p[6] >> 1) & 1
            if len(p) > 8 and isinstance(p[8], dict):
                self.obj_key.update(p[8].keys())
                keys_seen.update(p[8].keys())
        self.ac_has_key.update(keys_seen)
        if self.example is None and keys_seen:
            pts = [q for q in eu if len(q) > 8 and isinstance(q[8], dict)][:2]
            self.example = {"icao": d.get("icao"), "r": d.get("r"), "t": d.get("t"),
                            "timestamp": d.get("timestamp"), "n_points": len(tr),
                            "first_points": eu[:3], "points_with_object": pts}

    def report(self, title: str) -> None:
        """Print a plain-text summary."""
        print(f"\n===== {title} =====")
        print(f"files parsed={self.files} parse_errors={self.parse_err} "
              f"top-level keys={dict(self.top_keys)}")
        print(f"aircraft with points in Europe={self.eu_aircraft} | Europe points={self.eu_points:,}")
        if not self.eu_points:
            return
        pa = np.array(self.pts_per_ac)
        print(f"points per aircraft (Europe): median={np.median(pa):.0f} p10={np.percentile(pa, 10):.0f} "
              f"p90={np.percentile(pa, 90):.0f}")
        if self.gaps:
            g = np.array(self.gaps)
            print(f"gap between consecutive points (s): p10={np.percentile(g, 10):.1f} "
                  f"median={np.median(g):.1f} p90={np.percentile(g, 90):.1f} p99={np.percentile(g, 99):.1f} | "
                  f"<=5s {np.mean(g <= 5):.0%}, <=15s {np.mean(g <= 15):.0%}, "
                  f"<=30s {np.mean(g <= 30):.0%}, <=60s {np.mean(g <= 60):.0%}")
        print("share of Europe points that have each trace column:")
        print("  " + " | ".join(f"{name}={self.col[i] / self.eu_points:.0%}" for i, name in COLS.items()))
        print(f"  ground={self.ground / self.eu_points:.1%} stale-flag={self.stale / self.eu_points:.1%} "
              f"new-leg={self.newleg / self.eu_points:.2%}")
        print(f"position source/type: "
              + ", ".join(f"{k}={v / self.eu_points:.1%}" for k, v in self.src.most_common(6)))
        n_obj = self.col[8]
        print(f"aircraft-object present on {n_obj / self.eu_points:.0%} of points. Inside those objects, "
              f"share of objects containing each key (top 25):")
        print("  " + " | ".join(f"{k}={v / max(n_obj, 1):.0%}" for k, v in self.obj_key.most_common(25)))
        print("share of AIRCRAFT that report each key at least once (top 25):")
        print("  " + " | ".join(f"{k}={v / self.eu_aircraft:.0%}" for k, v in self.ac_has_key.most_common(25)))
        want = ["track_rate", "roll", "true_heading", "nav_heading", "nav_modes", "nav_altitude_mcp",
                "nav_altitude_fms", "wd", "ws", "oat", "tat", "tas", "mach", "ias", "category",
                "emergency"]
        print("KEY COVERAGE (share of objects / share of aircraft): " + " | ".join(
            f"{k}={self.obj_key[k] / max(n_obj, 1):.0%}/{self.ac_has_key[k] / self.eu_aircraft:.0%}"
            for k in want))
        print(f"most common aircraft types: {self.type_codes.most_common(8)}")
        if self.example:
            print("example aircraft (truncated):")
            print(json.dumps(self.example, default=str)[:1800])


def sample(tag: str, suffix_fn, offsets_mb: list[int], length: int, label: str
           ) -> tuple[Stats, dict[int, dict]]:
    """Download chunks at the given tar positions and analyse them."""
    stats = Stats()
    per_chunk: dict[int, dict] = {}
    for mb in offsets_mb:
        virt = (mb * 1_000_000) // 512 * 512
        url, start = suffix_fn(virt)
        path = f"{OUT_DIR}/{tag}_{mb}MB.bin"
        t0 = time.time()
        ok, msg = fetch(url, start, length, path)
        print(f"[{label}] @{mb} MB: {'OK' if ok else 'FAILED'} ({msg}) {time.time() - t0:.0f}s", flush=True)
        if not ok:
            continue
        data = open(path, "rb").read()
        members = parse_tar_chunk(data)
        names = [m[0] for m in members]
        print(f"   tar members fully inside chunk: {len(members)} | first names: {names[:3]}")
        per_chunk[mb] = {"sha": hashlib.sha256(data).hexdigest(), "names": names,
                         "hash_by_name": {n: hashlib.md5(b).hexdigest() for n, b in members}}
        for _, body in members:
            stats.add_file(body)
    return stats, per_chunk


def main() -> None:
    """Run the sample inspection."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026.09.22")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    offsets = FULL_MB if args.full else QUICK_MB
    length = 12_000_000 if args.full else 8_000_000
    mode = "full" if args.full else "quick"
    total_mb = (2 * len(offsets) + len(MLAT_MB[mode])) * length / 1e6
    print(f"day={args.day} mode={mode} | downloading about {total_mb:.0f} MB in total\n")

    results: dict[str, tuple[Stats, dict]] = {}
    for pod in ("prod-0", "staging-0"):
        tag = f"v{args.day}-planes-readsb-{pod}"

        def where(virt: int, tag: str = tag) -> tuple[str, int]:
            if virt < PART_BYTES:
                return f"{BASE}/{tag}/{tag}.tar.aa", virt
            return f"{BASE}/{tag}/{tag}.tar.ab", virt - PART_BYTES

        results[pod] = sample(tag, where, offsets, length, pod)

    mtag = f"v{args.day}-planes-readsb-mlatonly-0"
    results["mlatonly-0"] = sample(
        mtag, lambda v: (f"{BASE}/{mtag}/{mtag}.tar", v), MLAT_MB[mode], length, "mlatonly-0")

    for pod, (stats, _) in results.items():
        stats.report(f"{pod} (Europe only)")

    print("\n===== prod-0 vs staging-0: duplicates? =====")
    p, s = results["prod-0"][1], results["staging-0"][1]
    for mb in offsets:
        if mb not in p or mb not in s:
            print(f"@{mb} MB: missing chunk")
            continue
        if p[mb]["sha"] == s[mb]["sha"]:
            print(f"@{mb} MB: byte-for-byte IDENTICAL")
            continue
        common = set(p[mb]["names"]) & set(s[mb]["names"])
        same = sum(1 for n in common if p[mb]["hash_by_name"][n] == s[mb]["hash_by_name"][n])
        allnames = set(p[mb]["names"]) | set(s[mb]["names"])
        print(f"@{mb} MB: NOT identical | aircraft only in prod={len(set(p[mb]['names']) - common)} "
              f"only in staging={len(set(s[mb]['names']) - common)} in both={len(common)} "
              f"(identical content in {same}/{len(common)}) | union={len(allnames)}")


if __name__ == "__main__":
    main()
