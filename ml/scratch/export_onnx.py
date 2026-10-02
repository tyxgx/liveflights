"""Export a trained GRU (train_gru_v2.py) to ONNX and check that ONNX gives the same numbers as PyTorch.

The exported graph takes the RAW window tensors (exactly what the feature code produces, un-normalised):
    X (n, 10, 31)  float32   (variant A models ignore the columns after the first 12)
    S (n, 10)      float32
and returns   pos (n, 30, 2) km east/north relative to the last position,
              hdg (n, 5, 2) unit (sin, cos) of the track at +1..+5 min,
              spd (n, 5) m/s at +1..+5 min.
Normalisation, the straight-line baseline and the residual are INSIDE the graph, so the Lambda only has to
build X and S (shared feature module) and run the model.

Usage:
    pip install onnx onnxruntime
    python3 ml/scratch/export_onnx.py --run runs/B --data data/gru_v2 --out runs/B/model.onnx
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import torch
from torch import nn
from train_gru_v2 import N_CORE, N_EXTRA, N_MASK, GRUNet, Norm, forward_all, load_split


class Wrapper(nn.Module):
    """Raw X, S -> predictions (normalisation + baseline inside)."""

    def __init__(self, model: GRUNet, norm: Norm) -> None:
        super().__init__()
        self.model, self.norm = model, norm

    def forward(self, x, s):
        return forward_all(self.model, self.norm, x, s)


def load_norm(path: str) -> Norm:
    """Rebuild a Norm from norm.json without training data."""
    j = json.load(open(path))
    n = Norm.__new__(Norm)
    n.mean, n.std = np.array(j["mean"], np.float32), np.array(j["std"], np.float32)
    n.s_mean, n.s_std = np.array(j["s_mean"], np.float32), np.array(j["s_std"], np.float32)
    n.use_extra = j["use_extra"]
    n.use_dest = j.get("use_dest", False)
    n.s_num_dim = j.get("s_num_dim", 8)
    if n.use_dest:
        n.dest_mean, n.dest_std = j["dest_mean"], j["dest_std"]
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="folder with model.pt, norm.json, metrics.json")
    ap.add_argument("--data", default="data/gru_v2")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hidden", type=int, default=128)
    a = ap.parse_args()
    norm = load_norm(f"{a.run}/norm.json")
    n_in = N_CORE + (N_EXTRA + N_MASK if norm.use_extra else 0)
    model = GRUNet(n_in, a.hidden, s_num_dim=norm.s_num_dim)
    model.load_state_dict(torch.load(f"{a.run}/model.pt", map_location="cpu"))
    model.eval()
    net = Wrapper(model, norm).eval()

    te = load_split(a.data, "test")
    x = torch.from_numpy(te["X"][:2000])
    s = torch.from_numpy(te["S"][:2000])
    with torch.no_grad():
        ref = net(x, s)
    kw = {"input_names": ["X", "S"], "output_names": ["pos", "hdg", "spd"], "opset_version": 17,
          "dynamic_axes": {k: {0: "n"} for k in ("X", "S", "pos", "hdg", "spd")}}
    try:  # classic exporter first (no extra dependency); newer torch may only have the dynamo one
        torch.onnx.export(net, (x[:4], s[:4]), a.out, dynamo=False, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"classic exporter failed ({type(exc).__name__}: {str(exc)[:120]}); trying the default one")
        torch.onnx.export(net, (x[:4], s[:4]), a.out, **kw)
    import onnxruntime as ort
    sess = ort.InferenceSession(a.out, providers=["CPUExecutionProvider"])
    got = sess.run(None, {"X": x.numpy(), "S": s.numpy()})
    print("ONNX vs PyTorch, max abs difference on 2000 test windows:")
    for nm, r, g in zip(("pos km", "hdg", "spd m/s"), ref, got, strict=True):
        print(f"  {nm:8} {float(np.abs(r.numpy() - g).max()):.6f}")
    import os
    print(f"saved {a.out} ({os.path.getsize(a.out) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
