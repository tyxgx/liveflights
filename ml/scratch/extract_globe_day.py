"""Extract Europe airborne tracks from adsb.lol's public `globe_history` GitHub releases.

For each requested day it STREAMS the first `--mb` MB (default 1000) of the `prod-0` tar
(no raw file is written to disk). The tar is organised in folders `traces/<last 2 hex of
icao>/`, so the first ~1 GB is a ~25% random sample of the world's aircraft. Per aircraft it:

  * keeps only points inside the Europe box that are airborne, fresh (not flagged stale) and
    come from ADS-B (allow-list of source types); drops '~' addresses (non-ICAO / TIS-B),
  * resamples onto a fixed 10-second grid (linear interpolation, circular for angles),
    only where the neighbouring real points are close enough (no invented data across gaps),
  * forward-fills the sparse "aircraft object" fields (nav_*, wind, mach, ...) for a
    limited time, leaving NaN when unknown (NaN == "field not reported").

Units are converted to the same ones the live Lambda mapping uses (m, m/s). Output: one or
more Parquet parts per day in --out (default data/globe_extract/), plus a small
`<day>.done.json` marker with statistics (a finished day is skipped on re-runs).

Usage (from the repo root):
    python3 ml/scratch/extract_globe_day.py --dates 2026.09.15 --mb 60 --out data/globe_extract_smoke
    python3 ml/scratch/extract_globe_day.py --dates 2026.09.15
    python3 ml/scratch/extract_globe_day.py --round 1        # rounds 1..4, see ROUNDS
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import gzip
import json
import multiprocessing as mp
import os
import subprocess
import tarfile
import time

import numpy as np
import pandas as pd
from map_tar import curl_range, first_header  # same folder; tiny helpers for probing the tar

BASE ="https://github.com/adsblol/globe_history_{year}/releases/download"
# serving area is lat 35-64, lon -13..33; we extract a 3-degree MARGIN around it so that windows near the
# edge still have their 5-minute future (otherwise aircraft leaving the box would never be trained on)
LAT0, LAT1, LON0, LON1 = 32.0, 67.0, -16.0, 36.0
GRID_S = 10.0  # output grid spacing (seconds)
MAX_GAP_S = 30.0  # max distance between the two real points bracketing a grid time
SPARSE_GAP_S = 30.0  # same, for columns that are often missing (roll, ias, rates ...); 12 s lost
# most of them because cruise points are 15-30 s apart (smoke test: roll 29% instead of ~70%)
FFILL_S = 90.0  # how long an "aircraft object" value may be carried forward
ALLOWED_SRC = {"adsb_icao", "adsb_other", "adsr_icao"}
FT, KT, FPM = 0.3048, 0.514444, 0.00508
ROWS_PER_PART = 800_000
BATCH_BYTES = 24_000_000
MAX_INFLIGHT = 4  # batches being processed by workers while the download continues
PART_BYTES = 2_000_000_000  # the release tar is split into 2 GB parts (.aa, .ab)
SCHEMA = 5  # bump when the output columns/semantics change; days done with an older schema are redone
# schema 4: fast-changing object fields (track_rate, true_heading, mag_heading) are interpolated
# between the sparse samples instead of held for 90 s (validate_extras.py showed the held
# track_rate only correlated 0.53 with the real turn); tas/mach are held 30 s.
# schema 5: 3-degree box margin, OBJ_INTERP_GAP_S 60, freshness columns obj_age_s / trate_age_s
OBJ_INTERP_GAP_S = 60.0  # max distance between two object samples that may be interpolated
FAST_LINEAR = {"track_rate"}
FAST_ANGLE = {"true_heading", "mag_heading"}
MEDIUM_HOLD = {"tas_ms", "mach"}
MEDIUM_HOLD_S = 30.0
CALLSIGN_HOLD_S = 1800.0  # a callsign seen in the aircraft object is carried forward this long
HDR_BLOCK = 8_000_000  # header-walk requests fetch 8 MB at a time (each new request to GitHub can
# cost ~20 s of waiting before the first byte, so few big requests beat many tiny ones)
NAV_MODE_BITS = {"autopilot": 1, "vnav": 2, "althold": 4, "approach": 8, "lnav": 16, "tcas": 32}
OBJ_NUM_KEYS = {  # key inside the aircraft object -> (output column, multiplier)
    "track_rate": ("track_rate", 1.0), "true_heading": ("true_heading", 1.0),
    "mag_heading": ("mag_heading", 1.0), "mach": ("mach", 1.0), "tas": ("tas_ms", KT),
    "wd": ("wd_deg", 1.0), "ws": ("ws_ms", KT), "oat": ("oat_c", 1.0), "tat": ("tat_c", 1.0),
    "nav_altitude_mcp": ("nav_alt_mcp_m", FT), "nav_altitude_fms": ("nav_alt_fms_m", FT),
    "nav_heading": ("nav_heading", 1.0), "nav_qnh": ("nav_qnh", 1.0), "nic": ("nic", 1.0),
    "nac_p": ("nac_p", 1.0), "nac_v": ("nac_v", 1.0), "sil": ("sil", 1.0), "sda": ("sda", 1.0),
    "gva": ("gva", 1.0), "rc": ("rc", 1.0),
}
ROUNDS = {
    1: ["2026.09.01", "2026.09.03", "2026.09.05", "2026.09.07", "2026.09.09", "2026.09.11",
        "2026.09.13", "2026.09.15",  # train
        "2026.09.17",  # validation
        "2026.09.20", "2026.09.23"],  # test
    2: ["2026.08.27", "2026.08.28", "2026.08.29", "2026.08.30", "2026.08.31", "2026.09.02",
        "2026.09.04", "2026.09.06", "2026.09.08", "2026.09.10", "2026.09.12", "2026.09.14",
        "2026.09.16", "2026.09.18", "2026.09.19", "2026.09.21", "2026.09.22"],
    3: [f"2025.10.{d:02d}" for d in range(1, 24, 2)],
    4: ["2025.12.10", "2025.12.13", "2026.01.14", "2026.01.17", "2026.02.11", "2026.02.14",
        "2026.03.11", "2026.03.14", "2026.06.10", "2026.06.13"],
}


# ----------------------------------------------------------------------------- worker side
def _col(tr: list, i: int) -> np.ndarray:
    """Column i of a trace as float array (None -> NaN)."""
    return np.array([p[i] if len(p) > i and p[i] is not None else np.nan for p in tr], float)


def _interp_sparse(g: np.ndarray, t: np.ndarray, x: np.ndarray, max_gap: float) -> np.ndarray:
    """Linear interpolation of x(t) at g using only non-NaN samples; NaN across gaps."""
    m = ~np.isnan(x)
    out = np.full(g.shape, np.nan)
    if m.sum() < 2:
        return out
    tt, xx = t[m], x[m]
    k = np.searchsorted(tt, g)
    kk = np.clip(k, 1, len(tt) - 1)
    good = (k >= 1) & (k < len(tt)) & ((tt[kk] - tt[kk - 1]) <= max_gap)
    out[good] = np.interp(g[good], tt, xx)
    return out


def _hold(g: np.ndarray, times: list, vals: list, max_age: float) -> np.ndarray:
    """Last known value at or before each grid time, NaN if older than max_age."""
    out = np.full(g.shape, np.nan)
    if not times:
        return out
    tt, vv = np.asarray(times, float), np.asarray(vals, float)
    k = np.searchsorted(tt, g, side="right") - 1
    good = (k >= 0) & ((g - tt[np.clip(k, 0, len(tt) - 1)]) <= max_age)
    out[good] = vv[k[good]]
    return out


def _obj_field(col: str, g: np.ndarray, times: list, vals: list) -> np.ndarray:
    """One sparse aircraft-object field on the grid: interpolate fast-changing ones, hold others."""
    if not times:
        return np.full(g.shape, np.nan)
    t, v = np.asarray(times, float), np.asarray(vals, float)
    if col in FAST_LINEAR:
        return _interp_sparse(g, t, v, OBJ_INTERP_GAP_S)
    if col in FAST_ANGLE:
        s = _interp_sparse(g, t, np.sin(np.radians(v)), OBJ_INTERP_GAP_S)
        c = _interp_sparse(g, t, np.cos(np.radians(v)), OBJ_INTERP_GAP_S)
        return np.degrees(np.arctan2(s, c)) % 360.0
    return _hold(g, times, vals, MEDIUM_HOLD_S if col in MEDIUM_HOLD else FFILL_S)


def process_file(item: tuple[str, bytes]) -> dict | None:
    """Turn one per-aircraft trace file into 10 s grid rows (dict of arrays) or None."""
    name, raw = item
    try:
        d = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except (OSError, ValueError, EOFError):
        return {"err": 1}
    icao = str(d.get("icao") or "")
    tr = d.get("trace") or []
    if not icao or "~" in icao or len(tr) < 2:
        return None
    lat, lon = _col(tr, 1), _col(tr, 2)
    eu = (lat >= LAT0) & (lat <= LAT1) & (lon >= LON0) & (lon <= LON1)
    if int(eu.sum()) < 2:
        return None
    tr = [tr[i] for i in np.nonzero(eu)[0]]
    n_eu = len(tr)
    t = _col(tr, 0)
    flags = np.array([p[6] if len(p) > 6 and isinstance(p[6], int) else 0 for p in tr])
    ground = np.array([len(p) > 3 and p[3] == "ground" for p in tr])
    alt_ft = np.array([p[3] if len(p) > 3 and isinstance(p[3], (int, float)) else np.nan for p in tr])
    src_ok = np.array([len(p) > 9 and p[9] in ALLOWED_SRC for p in tr])
    stale = (flags & 1) > 0
    geo_alt_flag = (flags & 8) > 0  # trace altitude is geometric, not barometric
    geo_rate_flag = (flags & 4) > 0
    alt_baro = np.where(geo_alt_flag, np.nan, alt_ft)
    alt_geom = np.where(geo_alt_flag & np.isnan(_col(tr, 10)), alt_ft, _col(tr, 10))
    keep = ~ground & ~stale & src_ok & ~(np.isnan(alt_baro) & np.isnan(alt_geom))
    st = {"eu_pts": n_eu, "ground": int(ground.sum()), "stale": int(stale.sum()),
          "bad_src": int((~src_ok).sum())}
    if keep.sum() < 2:
        return {"stats": st}
    order = np.argsort(t[keep], kind="stable")
    sel = np.nonzero(keep)[0][order]
    trk = [tr[i] for i in sel]
    t = t[sel]
    legs = np.cumsum((flags[sel] & 2) > 0)
    baro_rate = np.where(geo_rate_flag[sel], np.nan, _col(trk, 7))
    geom_rate = np.where(geo_rate_flag[sel] & np.isnan(_col(trk, 11)), _col(trk, 7), _col(trk, 11))

    g0 = np.ceil(t[0] / GRID_S) * GRID_S
    g = np.arange(g0, t[-1], GRID_S)
    if len(g) == 0:
        return {"stats": st}
    k = np.searchsorted(t, g)
    kk = np.clip(k, 1, len(t) - 1)
    gap = t[kk] - t[kk - 1]
    ok = (k >= 1) & (k < len(t)) & (gap <= MAX_GAP_S) & (legs[kk] == legs[kk - 1])
    if not ok.any():
        return {"stats": st}
    g, gap = g[ok], gap[ok]

    lat_t, lon_t = _col(trk, 1), _col(trk, 2)
    trk_rad = np.radians(_col(trk, 5))
    sin_g = _interp_sparse(g, t, np.sin(trk_rad), MAX_GAP_S)
    cos_g = _interp_sparse(g, t, np.cos(trk_rad), MAX_GAP_S)
    cols: dict[str, np.ndarray] = {
        "ts": (float(d.get("timestamp") or 0.0) + g),
        "lat": np.interp(g, t, lat_t), "lon": np.interp(g, t, lon_t),
        "alt_baro_m": _interp_sparse(g, t, alt_baro[sel], MAX_GAP_S) * FT,
        "alt_geom_m": _interp_sparse(g, t, alt_geom[sel], SPARSE_GAP_S) * FT,
        "gs_ms": _interp_sparse(g, t, _col(trk, 4), MAX_GAP_S) * KT,
        "track_deg": np.degrees(np.arctan2(sin_g, cos_g)) % 360.0,
        "vrate_baro_ms": _interp_sparse(g, t, baro_rate, SPARSE_GAP_S) * FPM,
        "vrate_geom_ms": _interp_sparse(g, t, geom_rate, SPARSE_GAP_S) * FPM,
        "ias_ms": _interp_sparse(g, t, _col(trk, 12), SPARSE_GAP_S) * KT,
        "roll_deg": _interp_sparse(g, t, _col(trk, 13), SPARSE_GAP_S),
        "gap_s": gap,
    }
    # ---- sparse "aircraft object" fields: hold the last value for up to FFILL_S
    objs = [(float(p[0]), p[8]) for p in trk if len(p) > 8 and isinstance(p[8], dict)]
    series: dict[str, tuple[list, list]] = {}
    category = ""
    cs_t: list[float] = []
    cs_v: list[str] = []
    for tt, o in objs:
        for key, (col, mult) in OBJ_NUM_KEYS.items():
            v = o.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                series.setdefault(col, ([], []))
                series[col][0].append(tt)
                series[col][1].append(v * mult)
        fl = o.get("flight")
        if isinstance(fl, str) and fl.strip():
            cs_t.append(tt)
            cs_v.append(fl.strip())
        sq = o.get("squawk")
        if isinstance(sq, str) and sq.isdigit():
            series.setdefault("squawk", ([], []))
            series["squawk"][0].append(tt)
            series["squawk"][1].append(int(sq))
        nm = o.get("nav_modes")
        if isinstance(nm, list):
            series.setdefault("nav_modes", ([], []))
            series["nav_modes"][0].append(tt)
            series["nav_modes"][1].append(sum(NAV_MODE_BITS.get(x, 0) for x in nm))
        em = o.get("emergency")
        if isinstance(em, str):
            series.setdefault("emergency_flag", ([], []))
            series["emergency_flag"][0].append(tt)
            series["emergency_flag"][1].append(0 if em == "none" else 1)
        if not category and isinstance(o.get("category"), str):
            category = o["category"]
    all_cols = [c for c, _ in OBJ_NUM_KEYS.values()] + ["squawk", "nav_modes", "emergency_flag"]
    for col in all_cols:
        tv = series.get(col, ([], []))
        cols[col] = _obj_field(col, g, tv[0], tv[1])
    # freshness: seconds since the last object sample (any field), and to the nearest track_rate sample
    if objs:
        ot = np.array([o[0] for o in objs], float)
        ko = np.searchsorted(ot, g, side="right") - 1
        cols["obj_age_s"] = np.where(ko >= 0, g - ot[np.clip(ko, 0, len(ot) - 1)], np.nan)
    else:
        cols["obj_age_s"] = np.full(g.shape, np.nan)
    if "track_rate" in series:
        tt_r = np.asarray(series["track_rate"][0], float)
        kr = np.clip(np.searchsorted(tt_r, g), 1, max(len(tt_r) - 1, 1)) if len(tt_r) > 1 else None
        if kr is not None:
            cols["trate_age_s"] = np.minimum(np.abs(g - tt_r[kr]), np.abs(g - tt_r[kr - 1]))
        else:
            cols["trate_age_s"] = np.abs(g - tt_r[0])
    else:
        cols["trate_age_s"] = np.full(g.shape, np.nan)
    callsign = np.full(len(g), "", dtype=object)
    if cs_t:  # callsign: last one seen at or before each grid time (used later to look up routes)
        tt_a = np.asarray(cs_t, float)
        kc = np.searchsorted(tt_a, g, side="right") - 1
        okc = (kc >= 0) & ((g - tt_a[np.clip(kc, 0, len(tt_a) - 1)]) <= CALLSIGN_HOLD_S)
        callsign[okc] = np.asarray(cs_v, dtype=object)[kc[okc]]
    # readsb derives oat/tat/wind from other fields; obviously impossible values -> NaN
    for c, lo, hi in (("oat_c", -100.0, 60.0), ("tat_c", -100.0, 80.0),
                      ("ws_ms", 0.0, 150.0), ("mach", 0.0, 1.5),
                      ("alt_baro_m", -300.0, 16000.0), ("alt_geom_m", -300.0, 16500.0),
                      ("gs_ms", 0.0, 450.0), ("vrate_baro_ms", -60.0, 60.0),
                      ("vrate_geom_ms", -60.0, 60.0)):
        v = cols[c]
        cols[c] = np.where((v >= lo) & (v <= hi), v, np.nan)
    n = len(g)
    out: dict = {c: (a if c in ("ts", "lat", "lon") else a.astype(np.float32)) for c, a in cols.items()}
    out["icao"] = np.full(n, icao, dtype=object)
    out["dir2"] = np.full(n, icao[-2:], dtype=object)
    out["type_code"] = np.full(n, str(d.get("t") or ""), dtype=object)
    out["category"] = np.full(n, category, dtype=object)
    out["callsign"] = callsign
    out["db_flags"] = np.full(n, int(d.get("dbFlags") or 0), dtype=np.int16)
    meta = {"icao": icao, "dir2": icao[-2:], "reg": str(d.get("r") or ""),
            "type_code": str(d.get("t") or ""), "desc": str(d.get("desc") or ""),
            "operator": str(d.get("ownOp") or ""), "year": str(d.get("year") or ""),
            "db_flags": int(d.get("dbFlags") or 0), "callsign_first": cs_v[0] if cs_v else "",
            "rows": n}
    return {"cols": out, "n": n, "stats": st, "meta": meta}


def process_batch(batch: list[tuple[str, bytes]]) -> list:
    """Worker entry point: process a whole batch of files."""
    return [process_file(x) for x in batch]


# ----------------------------------------------------------------------------- main side
class CountingReader:
    """File-like wrapper that counts the bytes read from curl's stdout."""

    def __init__(self, f) -> None:
        self.f, self.n = f, 0

    def read(self, size: int = -1) -> bytes:
        b = self.f.read(size)
        self.n += len(b)
        return b


PROBES_MB = [150, 400, 750, 1050, 1400, 1800]  # where to look for the first traces/ member


def find_trace_start(url_aa: str) -> int:
    """Byte offset (512-aligned, a valid tar header) where the `traces/` section starts.

    The tar of a day is laid out as acas/ + heatmap/ (~35 files of ~25 MB, NOT aircraft data) +
    traces/xx/, in a different order on different days (heatmap first on 2026-09-01, last on
    2026-09-15; see ml/scratch/map_tar.py). We look at a few 256 KB pieces in parallel and start
    at the first piece that contains a `traces/` header. Returns 0 if none is found.
    """
    def probe(mb: int) -> tuple[int, tuple | None]:
        off = (mb * 1_000_000) // 512 * 512
        blk = curl_range(url_aa, off, 262_144)
        return off, (first_header(blk) if blk else None)

    with cf.ThreadPoolExecutor(len(PROBES_MB)) as ex:
        results = list(ex.map(probe, PROBES_MB))
    for off, fh in results:  # increasing offsets: take the first that shows traces
        if fh and "traces/" in fh[1]:
            return off + fh[0]
    return 0


class ChainedCurl:
    """File-like reader over a byte range of the split tar (.aa then .ab), via curl."""

    def __init__(self, base: str, start: int, nbytes: int) -> None:
        end = start + nbytes
        self.segs: list[tuple[str, int, int]] = []
        if start < PART_BYTES:
            self.segs.append((f"{base}.tar.aa", start, min(end, PART_BYTES) - 1))
        if end > PART_BYTES:
            self.segs.append((f"{base}.tar.ab", max(start, PART_BYTES) - PART_BYTES,
                              end - PART_BYTES - 1))
        self.n = 0
        self.err = ""
        self.proc: subprocess.Popen | None = None
        self._next()

    def _finish(self) -> None:
        if self.proc is not None:
            self.proc.stdout.close()
            self.proc.terminate()
            self.err += self.proc.stderr.read().decode(errors="replace").strip()[:200]
            self.proc.wait()
            self.proc = None

    def _next(self) -> bool:
        self._finish()
        if not self.segs:
            return False
        url, a, b = self.segs.pop(0)
        self.proc = subprocess.Popen(["curl", "-sSfL", "-r", f"{a}-{b}", url],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True

    def read(self, size: int = -1) -> bytes:
        """Read up to `size` bytes, moving on to the next part when one ends."""
        while self.proc is not None:
            b = self.proc.stdout.read(size)
            if b:
                self.n += len(b)
                return b
            if not self._next():
                break
        return b""

    def close(self) -> str:
        """Stop downloading; returns any curl error text."""
        self._finish()
        return self.err


def _range(url: str, start: int, length: int, retries: int = 3) -> bytes:
    """Fetch a small byte range with curl (b'' if it keeps failing or timing out)."""
    for _ in range(retries):
        try:
            r = subprocess.run(["curl", "-sSfL", "-r", f"{start}-{start + length - 1}", url],
                               capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode == 0 and r.stdout:
            return r.stdout
    return b""


def first_trace_offset(url_aa: str, url_ab: str, verbose: bool = False) -> int:
    """Byte offset of the first `traces/...` member of the tar.

    Some days' tars start with a HUGE non-trace member (about 900 MB on 2026-09-01); streaming
    the first N MB would then contain almost no aircraft. Walk the 512-byte tar headers and
    jump over such members with tiny range requests. Raises if the very first header cannot be
    read (better a visible error than silently streaming from byte 0 again).
    """
    off = 0
    blk, blk_start = b"", -1
    for _ in range(64):
        t1 = time.time()
        if not (blk and blk_start <= off and off + 512 <= blk_start + len(blk)):
            url, roff = (url_aa, off) if off < PART_BYTES else (url_ab, off - PART_BYTES)
            length = min(HDR_BLOCK, PART_BYTES - roff) if url == url_aa else HDR_BLOCK
            blk, blk_start = _range(url, roff, length), off
            if verbose:
                print(f"  fetched {len(blk):,} bytes starting at {off:,} ({time.time() - t1:.1f}s)",
                      flush=True)
        if len(blk) < 512 or off + 512 > blk_start + len(blk):
            if off == 0:
                raise RuntimeError("could not read the first tar header (network/URL problem)")
            return off
        h = blk[off - blk_start: off - blk_start + 512]
        if h == b"\0" * 512:
            return off
        name = h[:100].split(b"\0")[0].decode("utf8", "replace")
        prefix = h[345:500].split(b"\0")[0].decode("utf8", "replace")
        full = f"{prefix}/{name}" if prefix else name
        try:
            size = int(h[124:136].split(b"\0")[0].strip() or b"0", 8)
        except ValueError:
            return off
        if verbose:
            print(f"  header @ {off:>13,}: {full!r} type={h[156:157]!r} size={size:,} "
                  f"({time.time() - t1:.1f}s)", flush=True)
        if "traces/" in full:
            return off
        off += 512 + ((size + 511) // 512) * 512
    return off


def extract_day(date: str, a: argparse.Namespace, pool) -> None:
    """Download+process one day."""
    day = date.replace(".", "-")
    prefix = f"{a.out}/{day}"
    year = date[:4]
    tag = f"v{date}-planes-readsb-{a.pod}"
    url_aa = f"{BASE.format(year=year)}/{tag}/{tag}.tar.aa"
    url_ab = f"{BASE.format(year=year)}/{tag}/{tag}.tar.ab"
    marker = f"{prefix}.done.json"
    if os.path.exists(marker) and not a.force:
        try:
            with open(marker) as f:
                old = json.load(f)
        except ValueError:
            old = {}
        legacy = old.get("bytes_read", 0) / 1e6 if old.get("files", 0) >= 10_000 else 0
        old_mb = old.get("trace_mb", legacy)
        if old_mb >= 0.7 * a.mb and old.get("schema") == SCHEMA:
            print(f"[{date}] already done ({old_mb:.0f} MB of trace data), skipping", flush=True)
            return
        print(f"[{date}] earlier run looks incomplete ({old_mb:.0f} MB of traces), redoing",
              flush=True)
    if os.path.isdir(a.out):
        for fn in os.listdir(a.out):
            if fn.startswith((f"{day}_p", f"{day}_aircraft")) or fn == f"{day}.done.json":
                os.remove(f"{a.out}/{fn}")
    t0 = time.time()
    nbytes = int(a.mb * 1_000_000)
    start = find_trace_start(url_aa)
    print(f"[{date}] traces section starts ~{start / 1e6:.0f} MB into the tar; streaming from there "
          f"until {a.mb:.0f} MB of trace files are collected", flush=True)
    rd = ChainedCurl(url_aa[: -len(".tar.aa")], start, int(nbytes * 1.3))
    stats = {"files": 0, "files_eu": 0, "rows": 0, "eu_pts": 0, "ground": 0, "stale": 0,
             "bad_src": 0, "parse_err": 0, "trace_bytes": 0}
    dirs: set[str] = set()
    skipped: collections.Counter = collections.Counter()  # bytes per skipped top-level folder
    metas: list[dict] = []  # one row per aircraft: registration, operator, type name, year ...
    buf: list[dict] = []
    pending: collections.deque = collections.deque()
    buf_rows, part = 0, 0
    batch: list[tuple[str, bytes]] = []
    batch_bytes, next_report = 0, 50_000_000

    def flush(final: bool = False) -> None:
        nonlocal buf, buf_rows, part
        if not buf:
            return
        cols = list(buf[0].keys())
        df = pd.DataFrame({c: np.concatenate([b[c] for b in buf]) for c in cols})
        path = f"{prefix}_p{part:02d}.parquet"
        df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
        part += 1
        buf, buf_rows = [], 0

    def collect(results: list) -> None:
        nonlocal buf_rows
        for res in results:
            if res is None:
                continue
            if res.get("err"):
                stats["parse_err"] += 1
                continue
            for k, v in res.get("stats", {}).items():
                stats[k] += v
            if "cols" in res:
                stats["files_eu"] += 1
                stats["rows"] += res["n"]
                buf.append(res["cols"])
                metas.append(res["meta"])
                buf_rows += res["n"]
        if buf_rows >= ROWS_PER_PART:
            flush()

    def run_batch(drain: bool = False) -> None:
        """Hand the current batch to a worker WITHOUT waiting, so downloading and
        processing overlap; only wait when too many batches are in flight."""
        nonlocal batch, batch_bytes
        if batch:
            pending.append(pool.apply_async(process_batch, (batch,)))
            batch, batch_bytes = [], 0
        while pending and (drain or len(pending) > MAX_INFLIGHT):
            collect(pending.popleft().get())

    os.makedirs(a.out, exist_ok=True)
    try:
        with tarfile.open(fileobj=rd, mode="r|") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                base = os.path.basename(m.name)
                if "/traces/" not in m.name.replace("./", "/", 1) or not base.startswith("trace_full_"):
                    skipped[m.name.lstrip("./").split("/")[0] or "root"] += m.size  # not aircraft data
                    continue
                raw = tf.extractfile(m).read()
                stats["files"] += 1
                dirs.add(m.name.split("/")[-2])
                batch.append((m.name, raw))
                batch_bytes += len(raw)
                stats["trace_bytes"] += len(raw)
                if stats["trace_bytes"] >= nbytes:  # enough trace data: stop downloading
                    break
                if batch_bytes >= BATCH_BYTES or len(batch) >= 600:
                    run_batch()
                if rd.n >= next_report:
                    el = time.time() - t0
                    print(f"[{date}] {rd.n / 1e6:.0f} MB read, {stats['files']:,} files, "
                          f"{stats['files_eu']:,} Europe aircraft, {stats['rows']:,} rows, "
                          f"{rd.n / 1e6 / max(el, 1):.1f} MB/s", flush=True)
                    next_report = (rd.n // 100_000_000 + 1) * 100_000_000
    except (tarfile.ReadError, EOFError, OSError) as exc:  # truncated last member is expected
        print(f"[{date}] stream ended: {type(exc).__name__}: {str(exc)[:80]}", flush=True)
    run_batch(drain=True)
    flush(final=True)
    if metas:
        pd.DataFrame(metas).to_parquet(f"{prefix}_aircraft.parquet", engine="pyarrow",
                                       compression="zstd", index=False)
    err = rd.close()  # we usually stop before curl has sent everything
    if stats["files"] == 0:
        print(f"[{date}] FAILED (no trace files). curl said: {err}", flush=True)
        return
    secs = time.time() - t0
    out_mb = sum(os.path.getsize(f"{a.out}/{f}") for f in os.listdir(a.out)
                 if f.startswith(f"{day}_p")) / 1e6
    stats.update({"date": date, "pod": a.pod, "mb_requested": a.mb, "bytes_read": rd.n,
                  "dirs_seen": len(dirs), "seconds": round(secs), "out_mb": round(out_mb, 1),
                  "parts": part, "trace_mb": round(stats["trace_bytes"] / 1e6, 1),
                  "start_offset_mb": round(start / 1e6, 1), "schema": SCHEMA,
                  "skipped_mb": {k: round(v / 1e6, 1) for k, v in skipped.items()}})
    if stats["trace_bytes"] < 0.7 * nbytes:
        print(f"[{date}] WARNING: only {stats['trace_bytes'] / 1e6:.0f} MB of trace data "
              f"(wanted {a.mb:.0f}); the stream may have been cut. Running the same command "
              f"again will redo this day.", flush=True)
    with open(f"{prefix}.done.json", "w") as f:
        json.dump(stats, f)
    print(f"[{date}] DONE {secs:.0f}s | files={stats['files']:,} europe_aircraft={stats['files_eu']:,} "
          f"rows={stats['rows']:,} | dropped pts: ground={stats['ground']:,} stale={stats['stale']:,} "
          f"non-ADSB={stats['bad_src']:,} | dirs={len(dirs)} | output {out_mb:.0f} MB "
          f"in {part} part(s) | trace data read={stats['trace_bytes'] / 1e6:.0f} MB, "
          f"skipped non-trace MB={ {k: round(v / 1e6) for k, v in skipped.items()} }", flush=True)


def summarize(out: str, dates: list[str]) -> None:
    """Print one line per requested day from its .done.json (or say it has no result)."""
    print("\n=== SUMMARY (one line per day) ===")
    print(f"{'date':10} {'status':10} {'aircraft':>8} {'rows':>10} {'trace MB':>9} {'out MB':>7} "
          f"{'sec':>5} schema")
    for d in dates:
        p = f"{out}/{d.replace('.', '-')}.done.json"
        if not os.path.exists(p):
            print(f"{d:10} NO RESULT")
            continue
        with open(p) as f:
            s = json.load(f)
        ok = s.get("trace_mb", 0) >= 0.7 * s.get("mb_requested", 1000) and s.get("schema") == SCHEMA
        print(f"{d:10} {'OK' if ok else 'INCOMPLETE':10} {s.get('files_eu', 0):>8,} "
              f"{s.get('rows', 0):>10,} {s.get('trace_mb', 0):>9,.0f} {s.get('out_mb', 0):>7,.0f} "
              f"{s.get('seconds', 0):>5} {s.get('schema', '-')}")


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", help="comma separated, e.g. 2026.09.15,2026.09.17")
    ap.add_argument("--round", type=int, choices=sorted(ROUNDS))
    ap.add_argument("--mb", type=float, default=1000, help="MB of the tar to stream (default 1000)")
    ap.add_argument("--pod", default="prod-0")
    ap.add_argument("--out", default="data/globe_extract")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--probe", help="only walk the tar headers of this date (no download)")
    a = ap.parse_args()
    if a.probe:
        tag = f"v{a.probe}-planes-readsb-{a.pod}"
        base = f"{BASE.format(year=a.probe[:4])}/{tag}/{tag}"
        off = first_trace_offset(f"{base}.tar.aa", f"{base}.tar.ab", verbose=True)
        print(f"first trace member starts at byte {off:,} ({off / 1e6:.0f} MB)")
        return
    dates = a.dates.split(",") if a.dates else ROUNDS.get(a.round or 0, [])
    if not dates:
        ap.error("give --dates or --round")
    print(f"{len(dates)} day(s), {a.mb:.0f} MB each, pod={a.pod}, workers={a.workers}, out={a.out}",
          flush=True)
    t0 = time.time()
    failed: list[str] = []
    with mp.get_context("spawn").Pool(a.workers) as pool:
        for d in dates:
            try:
                extract_day(d, a, pool)
            except Exception as exc:  # noqa: BLE001 - one bad day must not stop the whole run
                print(f"[{d}] ERROR: {type(exc).__name__}: {exc}", flush=True)
                failed.append(d)
    summarize(a.out, dates)
    print(f"ALL DONE in {(time.time() - t0) / 60:.1f} min | days with errors: {failed or 'none'}",
          flush=True)


if __name__ == "__main__":
    main()
