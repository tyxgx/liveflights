"""Map how an adsb.lol globe_history day tarball is organised (read-only, tiny downloads).

The release tar is ~3-4 GB and has no index, so we sample it: every --step-mb MB we download
256 KB (in parallel, because every new request to GitHub can wait ~20 s before the first byte),
find the first valid tar header inside that piece (verified with the tar checksum) and print
which file/folder lives there. From that you can see the sections (acas/, heatmap/, traces/xx/,
...), their order, how big each is, and how the layout differs between days.

Usage (from the repo root):
    python3 ml/scratch/map_tar.py --dates 2026.09.01,2026.09.15
    python3 ml/scratch/map_tar.py --dates 2026.09.01 --step-mb 100 --workers 4
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import subprocess
import time

BASE = "https://github.com/adsblol/globe_history_{year}/releases/download"
API = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags"
PART = 2_000_000_000
PIECE = 262_144


def curl_range(url: str, start: int, length: int) -> bytes:
    """Download bytes [start, start+length) (b'' after 2 failed attempts)."""
    for _ in range(2):
        try:
            r = subprocess.run(["curl", "-sSfL", "-r", f"{start}-{start + length - 1}", url],
                               capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode == 0 and r.stdout:
            return r.stdout
    return b""


def valid_header(h: bytes) -> bool:
    """Tar header checksum check (unsigned sum of all bytes, checksum field counted as spaces)."""
    try:
        stored = int(h[148:156].split(b"\0")[0].strip() or b"x", 8)
    except ValueError:
        return False
    return stored == sum(h[:148]) + 8 * 32 + sum(h[156:512])


def first_header(block: bytes) -> tuple[int, str, int, str] | None:
    """(position, name, size, type) of the first valid header inside a block, if any."""
    for i in range(0, len(block) - 511, 512):
        h = block[i:i + 512]
        if h[257:262] == b"ustar" and valid_header(h):
            name = h[:100].split(b"\0")[0].decode("utf8", "replace")
            prefix = h[345:500].split(b"\0")[0].decode("utf8", "replace")
            try:
                size = int(h[124:136].split(b"\0")[0].strip() or b"0", 8)
            except ValueError:
                size = -1
            return i, (f"{prefix}/{name}" if prefix else name), size, h[156:157].decode("latin1")
    return None


def sizes(year: str, tag: str) -> list[int]:
    """Sizes of the release assets (.tar.aa, .tar.ab) from the GitHub API."""
    r = subprocess.run(["curl", "-sS", "-m", "90", f"{API.format(year=year)}/{tag}"],
                       capture_output=True, text=True)
    try:
        assets = json.loads(r.stdout)["assets"]
    except (ValueError, KeyError):
        return [PART, 1_900_000_000]
    return [a["size"] for a in sorted(assets, key=lambda a: a["name"]) if ".tar." in a["name"]]


def sample_one(base: str, virt: int) -> dict:
    """Fetch one piece at a virtual tar offset and describe the first header found."""
    url, roff = (f"{base}.tar.aa", virt) if virt < PART else (f"{base}.tar.ab", virt - PART)
    t0 = time.time()
    blk = curl_range(url, roff, PIECE)
    fh = first_header(blk) if blk else None
    return {"virt": virt, "sec": time.time() - t0, "hdr": fh, "got": len(blk)}


def section(name: str) -> str:
    """Top-level section of a member path."""
    parts = [p for p in name.split("/") if p and p != "."]
    if not parts:
        return "(root)"
    return f"{parts[0]}/{parts[1]}" if parts[0] == "traces" and len(parts) > 1 else parts[0]


def map_day(date: str, step_mb: int, workers: int) -> None:
    """Print the layout of one day's tar."""
    tag = f"v{date}-planes-readsb-prod-0"
    base = f"{BASE.format(year=date[:4])}/{tag}/{tag}"
    sz = sizes(date[:4], tag)
    total = sum(sz)
    print(f"\n=========== {date} | assets {sz} | tar total {total / 1e6:,.0f} MB ===========", flush=True)
    offs = [(o // 512) * 512 for o in range(0, total - PIECE, step_mb * 1_000_000)]
    t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        res = sorted(ex.map(lambda o: sample_one(base, o), offs), key=lambda r: r["virt"])
    print(f"(sampled {len(offs)} places in {time.time() - t0:.0f}s)")
    print(f"{'offset MB':>10} | {'fetch s':>7} | first header found in that piece")
    prev = None
    for r in res:
        if not r["hdr"]:
            print(f"{r['virt'] / 1e6:>10,.0f} | {r['sec']:>7.0f} | (nothing readable, got {r['got']:,} bytes)")
            continue
        pos, name, size, typ = r["hdr"]
        sec = section(name)
        mark = "" if sec == prev else "   <-- new section"
        print(f"{(r['virt'] + pos) / 1e6:>10,.0f} | {r['sec']:>7.0f} | {name}  (type {typ}, "
              f"{size / 1e6:.2f} MB){mark}")
        prev = sec
    kinds: dict[str, list[float]] = collections.defaultdict(list)
    for r in res:
        if r["hdr"]:
            top = section(r["hdr"][1]).split("/")[0]
            kinds[top].append((r["virt"] + r["hdr"][0]) / 1e6)
    print("\nsummary (approximate, resolution = sampling step):")
    for k, v in kinds.items():
        print(f"  {k:10s}: seen at {len(v)} sample(s), from ~{min(v):,.0f} MB to ~{max(v):,.0f} MB")
    dirs = [section(r["hdr"][1]) for r in res if r["hdr"] and r["hdr"][1].find("traces/") >= 0]
    print(f"  distinct traces/xx folders among samples: {len(set(dirs))} of {len(dirs)} samples")


def main() -> None:
    """CLI."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="2026.09.01,2026.09.15")
    ap.add_argument("--step-mb", type=int, default=150)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    for d in a.dates.split(","):
        map_day(d.strip(), a.step_mb, a.workers)


if __name__ == "__main__":
    main()
