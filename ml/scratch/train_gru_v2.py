"""Train the GRU trajectory model on the windows made by build_windows_v2.py (runs on Colab GPU or locally).

The network predicts a RESIDUAL over the straight-line physics guess (so it only has to learn what
physics gets wrong: turns, climbs, speed changes), for 30 future positions (every 10 s, +5 min), plus the
heading and speed at each whole minute (used to draw a smooth curved path on the dashboard).

Variants (ablation, `--variant`):
    A  core features only (the 12 fields our live pipeline already has: position, speed, track, vertical
       rate, altitude, per-step changes)
    B  core + extra fields (roll, track_rate, heading-minus-track, mach, tas, ias, wind, autopilot deltas)
       + their masks (1 = the aircraft reported it; missing values are 0)

Everything is reported per regime (level / turning / climb-descent), per horizon (+1..+5 min), and for
seen/unseen aircraft on the test days, always next to the straight-line baseline.

Usage:
    python3 ml/scratch/train_gru_v2.py --data data/gru_v2_smoke --epochs 2 --variant B --out runs/smoke   # logic test
    python3 ml/scratch/train_gru_v2.py --data /content/gru_v2 --variant B --epochs 15 --out /content/runs/B
    python3 ml/scratch/train_gru_v2.py --data /content/gru_v2 --variant A --epochs 15 --out /content/runs/A
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import time

import numpy as np
import pandas as pd
import torch
from torch import nn

N_CORE, N_EXTRA, N_MASK = 12, 10, 9
MINUTE_IDX = [5, 11, 17, 23, 29]
T_S = torch.arange(1, 31, dtype=torch.float32) * 10.0  # seconds ahead of each of the 30 targets
EXTRA_TO_MASK = [0, 1, 2, 3, 4, 5, 6, 6, 7, 8]  # extra value j is valid when mask EXTRA_TO_MASK[j] == 1
POS_SCALE = 10.0  # residual position is learned in units of 10 km
SPD_SCALE = 50.0  # speed change learned in units of 50 m/s
REGIMES = {0: "level", 1: "turning", 2: "climb/desc"}


# ----------------------------------------------------------------------------- data
def load_split(root: str, split: str) -> dict | None:
    """Concatenate all days of one split. Returns dict of numpy arrays + meta DataFrame."""
    days = sorted(glob.glob(f"{root}/{split}/*/"))
    if not days:
        return None
    parts = {k: [] for k in ("X", "S", "Y", "YH", "YV")}
    metas = []
    for d in days:
        for k in parts:
            parts[k].append(np.load(f"{d}{k}.npy"))
        m = pd.read_parquet(f"{d}meta.parquet")
        m["day"] = os.path.basename(d.rstrip("/"))
        metas.append(m)
    out = {k: np.concatenate(v) for k, v in parts.items()}
    out["meta"] = pd.concat(metas, ignore_index=True)
    return out


def straight_baseline(x: torch.Tensor) -> torch.Tensor:
    """Straight-line guess (n, 30, 2) in km from the raw (un-normalised) last step: gs, sin, cos."""
    gs, sn, cs = x[:, -1, 2], x[:, -1, 3], x[:, -1, 4]
    t = T_S.to(x.device)[None, :]
    return torch.stack([gs[:, None] * t * sn[:, None], gs[:, None] * t * cs[:, None]], -1) / 1000.0


class Norm:
    """Per-feature normalisation of X (extras use only the values that were reported) and S."""

    def __init__(self, x: np.ndarray, s: np.ndarray, use_extra: bool) -> None:
        rng = np.random.default_rng(0)
        sub = rng.choice(len(x), size=min(len(x), 200_000), replace=False)
        xs, ss = x[sub].reshape(-1, x.shape[-1]), s[sub]
        self.use_extra = use_extra
        self.use_dest = s.shape[-1] == 14  # schema v2: S has 4 extra destination-route columns
        self.s_num_dim = 12 if self.use_dest else 8
        self.mean = np.zeros(x.shape[-1], np.float32)
        self.std = np.ones(x.shape[-1], np.float32)
        for j in range(N_CORE):
            self.mean[j], self.std[j] = xs[:, j].mean(), xs[:, j].std()
        for j in range(N_EXTRA):
            ok = xs[:, N_CORE + N_EXTRA + EXTRA_TO_MASK[j]] > 0.5
            if ok.sum() > 100:
                self.mean[N_CORE + j], self.std[N_CORE + j] = xs[ok, N_CORE + j].mean(), xs[ok, N_CORE + j].std()
        self.std[self.std < 1e-6] = 1.0
        self.s_mean, self.s_std = ss[:, :6].mean(0), ss[:, :6].std(0)
        self.s_std[self.s_std < 0.05] = 1.0  # a (nearly) constant feature (e.g. day of week in a 1-day set) must not be blown up
        if self.use_dest:
            # dest_dist_norm (col 10) is already ~[0,1.4]; only re-centre/scale it using the rows
            # where a destination WAS found (col 13 = m_dest), like the X extras
            known = ss[:, 13] > 0.5
            if known.sum() > 100:
                self.dest_mean, self.dest_std = ss[known, 10].mean(), ss[known, 10].std()
                self.dest_std = self.dest_std if self.dest_std > 1e-6 else 1.0
            else:
                self.dest_mean, self.dest_std = 0.0, 1.0

    def x(self, x: torch.Tensor) -> torch.Tensor:
        """Normalise; missing extras become exactly 0; drop extras for variant A."""
        m, sd = torch.from_numpy(self.mean).to(x.device), torch.from_numpy(self.std).to(x.device)
        z = (x - m) / sd
        if not self.use_extra:
            return z[..., :N_CORE].clamp(-10, 10)
        vals = z[..., N_CORE:N_CORE + N_EXTRA]
        masks = x[..., N_CORE + N_EXTRA:]
        keep = masks[..., EXTRA_TO_MASK]
        z = torch.cat([z[..., :N_CORE], vals * keep, masks], -1)
        return z.clamp(-10, 10)

    def s(self, s: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Numeric static features (base 8: 6 normalised + is_heli + is_mil; +4 destination-route
        columns when use_dest), category id, wake id (+1)."""
        m, sd = torch.from_numpy(self.s_mean).to(s.device), torch.from_numpy(self.s_std).to(s.device)
        base = torch.cat([((s[:, :6] - m) / sd).clamp(-10, 10), s[:, 8:10]], 1)
        if self.use_dest:
            known = s[:, 13:14]
            dist = ((s[:, 10:11] - self.dest_mean) / self.dest_std).clamp(-10, 10) * known
            base = torch.cat([base, dist, s[:, 11:13] * known, known], 1)  # dist, sin, cos, mask
        return base, s[:, 6].long().clamp(0, 24), (s[:, 7].long() + 1).clamp(0, 4)

    def to_json(self) -> dict:
        j = {"mean": self.mean.tolist(), "std": self.std.tolist(), "s_mean": self.s_mean.tolist(),
             "s_std": self.s_std.tolist(), "use_extra": self.use_extra, "use_dest": self.use_dest,
             "s_num_dim": self.s_num_dim}
        if self.use_dest:
            j["dest_mean"], j["dest_std"] = float(self.dest_mean), float(self.dest_std)
        return j


# ----------------------------------------------------------------------------- model
class GRUNet(nn.Module):
    """GRU over the 10 history steps + static embedding -> residual positions, heading, speed."""

    def __init__(self, n_in: int, hidden: int = 128, layers: int = 2, dropout: float = 0.1,
                s_num_dim: int = 8) -> None:
        super().__init__()
        self.inp = nn.Sequential(nn.Linear(n_in, hidden), nn.GELU())
        self.gru = nn.GRU(hidden, hidden, layers, batch_first=True, dropout=dropout)
        self.cat_emb, self.wake_emb = nn.Embedding(25, 8), nn.Embedding(5, 4)
        self.head = nn.Sequential(nn.Linear(hidden + 8 + 4 + s_num_dim + hidden, 256), nn.GELU(),
                                  nn.Dropout(dropout), nn.Linear(256, 256), nn.GELU())
        self.pos = nn.Linear(256, 60)  # 30 x (east, north) residual
        self.hdg = nn.Linear(256, 10)  # 5 x (sin, cos) of the track at whole minutes
        self.spd = nn.Linear(256, 5)  # speed change vs the last speed at whole minutes

    def forward(self, x, s_num, cat, wake):
        h, _ = self.gru(self.inp(x))
        last = h[:, -1]
        z = torch.cat([last, self.cat_emb(cat), self.wake_emb(wake), s_num, self.inp(x[:, -1])], 1)
        z = self.head(z)
        return (self.pos(z).view(-1, 30, 2), self.hdg(z).view(-1, 5, 2), self.spd(z))


def forward_all(model, norm, x_raw, s_raw):
    """Raw batch -> predicted absolute positions (n,30,2 km), headings (n,5,2 unit), speeds (n,5)."""
    x = norm.x(x_raw)
    s_num, cat, wake = norm.s(s_raw)
    res, hdg, spd = model(x, s_num, cat, wake)
    pos = straight_baseline(x_raw) + res * POS_SCALE
    hdg = hdg / hdg.norm(dim=-1, keepdim=True).clamp_min(1e-6)
    return pos, hdg, x_raw[:, -1, 2:3] + spd * SPD_SCALE


# ----------------------------------------------------------------------------- train / eval
def to_dev(d: dict, dev: str) -> dict:
    """Tensors on `dev` ("cpu" = stream mode: data stays in RAM, only each batch goes to the GPU)."""
    out = {k: torch.from_numpy(d[k]).to(dev) for k in ("X", "S", "Y", "YH", "YV")}
    out["meta"] = d["meta"]
    return out


def batches(n: int, bs: int, shuffle: bool, dev: str):
    idx = torch.randperm(n, device=dev) if shuffle else torch.arange(n, device=dev)
    for i in range(0, n, bs):
        yield idx[i:i + bs]


def loss_fn(pos, hdg, spd, y, yh, yv, x_raw):
    """Huber on positions (all 30 points, +5 min counts extra), cosine loss on heading, Huber on speed."""
    w = torch.ones(30, device=y.device)
    w[MINUTE_IDX] = 2.0
    w[-1] = 4.0
    lp = (nn.functional.smooth_l1_loss(pos / POS_SCALE, y / POS_SCALE, beta=0.1, reduction="none")
          .sum(-1) * w).sum(-1).mean() / w.sum()
    lh = (1.0 - (hdg * yh).sum(-1)).mean()
    ls = nn.functional.smooth_l1_loss((spd - yv) / SPD_SCALE, torch.zeros_like(spd), beta=0.1)
    return lp + 0.2 * lh + 0.1 * ls, lp


@torch.no_grad()
def predict(model, norm, d: dict, bs: int = 4096):
    model.eval()
    outs = []
    dev = next(model.parameters()).device
    for i in range(0, len(d["X"]), bs):
        pos, hdg, spd = forward_all(model, norm, d["X"][i:i + bs].to(dev), d["S"][i:i + bs].to(dev))
        outs.append(pos.to(d["Y"].device))
    return torch.cat(outs)


def eval_split(model, norm, d: dict, name: str) -> dict:
    """Errors (km) at the whole-minute marks: model vs straight line, overall and by stratum."""
    pos = predict(model, norm, d)
    base = straight_baseline(d["X"])
    err_m = torch.linalg.norm(pos[:, MINUTE_IDX] - d["Y"][:, MINUTE_IDX], dim=-1).cpu().numpy()  # (n, 5)
    err_b = torch.linalg.norm(base[:, MINUTE_IDX] - d["Y"][:, MINUTE_IDX], dim=-1).cpu().numpy()
    meta = d["meta"]
    res: dict = {"n": len(meta)}

    def stat(mask: np.ndarray) -> dict:
        if mask.sum() == 0:
            return {}
        e5m, e5b = err_m[mask, 4], err_b[mask, 4]
        return {"n": int(mask.sum()), "model_median": float(np.median(e5m)), "model_mean": float(e5m.mean()),
                "model_p90": float(np.quantile(e5m, .9)), "model_p99": float(np.quantile(e5m, .99)),
                "straight_median": float(np.median(e5b)), "straight_mean": float(e5b.mean()),
                "straight_p90": float(np.quantile(e5b, .9)), "straight_p99": float(np.quantile(e5b, .99)),
                "win_rate": float((e5m < e5b).mean())}

    allm = np.ones(len(meta), bool)
    res["all"] = stat(allm)
    for r, nm in REGIMES.items():
        res[nm] = stat((meta["regime"].to_numpy() == r))
    if "unseen" in meta:
        res["unseen_aircraft"] = stat(meta["unseen"].to_numpy().astype(bool))
        res["seen_aircraft"] = stat(~meta["unseen"].to_numpy().astype(bool))
    res["by_minute_median"] = {"model": [float(np.median(err_m[:, k])) for k in range(5)],
                               "straight": [float(np.median(err_b[:, k])) for k in range(5)]}
    print(f"\n[{name}] n={len(meta):,}  error at +5 min (km):")
    for k in ("all", "level", "turning", "climb/desc", "unseen_aircraft"):
        s = res.get(k)
        if s:
            print(f"  {k:16} n={s['n']:>7,} | model median {s['model_median']:.2f} mean {s['model_mean']:.2f} "
                  f"p90 {s['model_p90']:.2f} p99 {s['model_p99']:.2f} | straight median {s['straight_median']:.2f} "
                  f"mean {s['straight_mean']:.2f} p90 {s['straight_p90']:.2f} p99 {s['straight_p99']:.2f} "
                  f"| model wins {100 * s['win_rate']:.0f}%")
    print("  median error by minute  model:", [round(v, 2) for v in res["by_minute_median"]["model"]],
          " straight:", [round(v, 2) for v in res["by_minute_median"]["straight"]])
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/gru_v2")
    ap.add_argument("--variant", choices=["A", "B"], default="B")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--bs", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--train-frac", type=float, default=1.0, help="use only this share of the train windows (learning curve)")
    ap.add_argument("--stream", action="store_true", help="keep the data in RAM and move only each batch to the GPU (small-memory Mac)")
    ap.add_argument("--test-max", type=int, default=0, help="use only this many random test windows (quick local runs)")
    ap.add_argument("--out", default="runs/gru")
    a = ap.parse_args()
    torch.manual_seed(0)
    dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    os.makedirs(a.out, exist_ok=True)
    print(f"device={dev} variant={a.variant} data={a.data}")

    tr, va, te = (load_split(a.data, s) for s in ("train", "val", "test"))
    if va is None:  # smoke datasets have no val split: hold out the last 10% of train
        k = int(len(tr["X"]) * 0.9)
        va = {key: (v[k:] if key != "meta" else v.iloc[k:].reset_index(drop=True)) for key, v in tr.items()}
        tr = {key: (v[:k] if key != "meta" else v.iloc[:k].reset_index(drop=True)) for key, v in tr.items()}
    if a.train_frac < 1.0:
        k = int(len(tr["X"]) * a.train_frac)
        idx = np.random.default_rng(1).permutation(len(tr["X"]))[:k]
        tr = {key: (v[idx] if key != "meta" else v.iloc[idx].reset_index(drop=True)) for key, v in tr.items()}
    if a.test_max and te is not None and len(te["X"]) > a.test_max:
        idx = np.sort(np.random.default_rng(2).permutation(len(te["X"]))[:a.test_max])
        te = {key: (v[idx] if key != "meta" else v.iloc[idx].reset_index(drop=True)) for key, v in te.items()}
    print(f"train {len(tr['X']):,}  val {len(va['X']):,}  test {len(te['X']) if te else 0:,}")

    norm = Norm(tr["X"], tr["S"], use_extra=a.variant == "B")
    n_in = N_CORE + (N_EXTRA + N_MASK if a.variant == "B" else 0)
    ddev = "cpu" if a.stream else dev
    tr, va = to_dev(tr, ddev), to_dev(va, ddev)
    te = to_dev(te, ddev) if te else None
    model = GRUNet(n_in, a.hidden, s_num_dim=norm.s_num_dim).to(dev)
    print(f"parameters: {sum(p.numel() for p in model.parameters()):,}")
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * ((len(tr["X"]) + a.bs - 1) // a.bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.1)
    best, hist = 1e9, []
    for ep in range(a.epochs):
        model.train()
        t0, tot, cnt = time.time(), 0.0, 0
        for ix in batches(len(tr["X"]), a.bs, True, ddev):
            xr, sr = tr["X"][ix].to(dev), tr["S"][ix].to(dev)
            pos, hdg, spd = forward_all(model, norm, xr, sr)
            loss, lp = loss_fn(pos, hdg, spd, tr["Y"][ix].to(dev), tr["YH"][ix].to(dev),
                               tr["YV"][ix].to(dev), xr)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot, cnt = tot + float(lp.detach()), cnt + 1
        pv = predict(model, norm, va)
        ev = torch.linalg.norm(pv[:, MINUTE_IDX[-1]] - va["Y"][:, MINUTE_IDX[-1]], dim=-1)
        v_mean, v_med = float(ev.mean()), float(ev.median())
        hist.append({"epoch": ep + 1, "train_pos_loss": tot / cnt, "val_mean_km": v_mean, "val_median_km": v_med})
        print(f"epoch {ep + 1:>2}/{a.epochs} | train loss {tot / cnt:.4f} | val +5min mean {v_mean:.3f} km "
              f"median {v_med:.3f} km | {time.time() - t0:.0f}s", flush=True)
        if v_mean < best:
            best = v_mean
            torch.save(model.state_dict(), f"{a.out}/model.pt")
    model.load_state_dict(torch.load(f"{a.out}/model.pt", map_location=dev))
    report = {"variant": a.variant, "epochs": a.epochs, "train_windows": len(tr["X"]), "history": hist,
              "val": eval_split(model, norm, va, "val")}
    if te is not None:
        report["test"] = eval_split(model, norm, te, "test")
    with open(f"{a.out}/metrics.json", "w") as f:
        json.dump(report, f, indent=1)
    with open(f"{a.out}/norm.json", "w") as f:
        json.dump(norm.to_json(), f)
    print(f"\nsaved {a.out}/model.pt, metrics.json, norm.json")


if __name__ == "__main__":
    main()
