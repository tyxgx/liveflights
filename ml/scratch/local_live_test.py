"""Test the trained model against REAL live adsb.lol data, entirely on this machine — no AWS,
no S3, no Lambda, no deploy. This is the fastest way to get a genuine "predicted vs actual" proof
point before writing/running any terraform.

What it does, every 60 seconds, for `--minutes` minutes:
  1. Fetches the real live adsb.lol feed (the SAME fetch code the ingest Lambda uses: 8 Europe
     hub points, staggered, retried on 429) — a plain public HTTP call, no AWS credentials needed.
  2. Extracts the ML fields per aircraft (`adsb_lol_extras_mapping.map_to_ml_fields`) and appends
     them to a rolling history — but as a local JSON file (`--out`/history.json) instead of
     `live/history.json` on S3. This re-runs the ingest Lambda's real `_update_history()` code
     unmodified, only its S3 client is swapped for a tiny local-file stand-in.
  3. Runs the real predict Lambda code (`predict/handler.py`'s `handler()`) unmodified, same
     swap: `live/predictions.json`, `live/pending.json`, `metrics/daily.json` all become local
     files instead of S3 objects. Uses the exported ONNX model from `--model-dir` (default
     runs/full_B) instead of S3's models/trajectory.onnx.
Because both real Lambdas' actual code runs untouched here (only their `s3` client is redirected),
a clean run of this script is real evidence the Lambda code itself works, not just the local test
harness — the only things NOT exercised are AWS IAM, EventBridge scheduling and the container
build, which is exactly the deploy-only part left for later.

After ~10-15 minutes enough history exists for the first predictions; after ~15-20 more minutes
the first evaluations (prediction vs what actually happened 5 minutes later) appear. Prints a
running status every minute and a summary at the end.

Usage (from the repo root):
    python3 ml/scratch/local_live_test.py --minutes 30
    python3 ml/scratch/local_live_test.py --minutes 30 --model-dir runs/full_B_dest  # try another model
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import types
from typing import Any

# This Mac's python.org build ships without root certs, so urllib HTTPS calls fail with
# CERTIFICATE_VERIFY_FAILED (a known local-machine quirk, documented in the fish-shell-gotchas
# memory; NOT an issue in the real Lambda, whose runtime has proper certs) -- fixed here, in this
# LOCAL TEST SCRIPT ONLY, by pointing urllib's default HTTPS context at certifi's CA bundle. The
# real handler.py files are never touched for this.
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    print("certifi not installed (pip install certifi) -- live HTTPS fetches may fail with "
         "CERTIFICATE_VERIFY_FAILED on this Mac", file=sys.stderr)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _ClientError(Exception):
    """Stand-in for botocore.exceptions.ClientError (only the .response shape used here)."""

    def __init__(self, response: dict) -> None:
        self.response = response
        super().__init__(str(response))


class LocalFileS3:
    """Same get_object/put_object shape as boto3's S3 client, backed by local JSON files under
    `out_dir` instead of a bucket — every S3 key (e.g. "live/history.json") maps to
    `out_dir/live__history.json` (slashes replaced so no subdirectories are needed)."""

    def __init__(self, out_dir: str) -> None:
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.out_dir, key.replace("/", "__"))

    def get_object(self, Bucket: str, Key: str) -> dict:
        path = self._path(Key)
        if not os.path.exists(path):
            raise _ClientError({"Error": {"Code": "NoSuchKey"}})
        with open(path, "rb") as f:
            data = f.read()
        return {"Body": types.SimpleNamespace(read=lambda: data)}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str = "") -> None:
        with open(self._path(Key), "wb") as f:
            f.write(Body)


def _stub_aws_and_model(model_dir: str, s3: LocalFileS3) -> None:
    """Install fake boto3/botocore modules (this machine has neither installed, and needs
    neither — nothing here ever touches real AWS), pre-load the model bytes straight from disk
    into predict.handler's module-level cache (skipping its S3-backed cold-start load), and point
    both Lambdas' `s3` client at the same LocalFileS3 instance so they share one local "bucket"."""
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name, *a, **k: s3  # every client() call returns the same local stub
    sys.modules["boto3"] = fake_boto3
    fake_botocore = types.ModuleType("botocore")
    fake_exceptions = types.ModuleType("botocore.exceptions")
    fake_exceptions.ClientError = _ClientError
    fake_botocore.exceptions = fake_exceptions
    sys.modules["botocore"] = fake_botocore
    sys.modules["botocore.exceptions"] = fake_exceptions

    os.environ.setdefault("FIREHOSE_STREAM_NAME", "local-test-unused")
    os.environ.setdefault("LAKE_BUCKET_NAME", "local-test-unused")

    sys.path.insert(0, os.path.join(REPO_ROOT, "infra", "terraform", "lambda_ingest"))
    sys.path.insert(0, os.path.join(REPO_ROOT, "predict"))
    sys.path.insert(0, os.path.join(REPO_ROOT, "ml"))
    sys.path.insert(0, REPO_ROOT)

    import handler as ingest_handler  # infra/terraform/lambda_ingest/handler.py, unmodified
    import handler as predict_handler  # NOTE: same module name as above; see the import dance below

    # both files are literally named handler.py, so re-importing the second one under the same
    # name would just return the already-cached ingest module. Load each explicitly by file path
    # instead, so both real, unmodified handlers are available at once.
    import importlib.util

    def _load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    ingest_handler = _load("ingest_handler", os.path.join(REPO_ROOT, "infra", "terraform",
                                                          "lambda_ingest", "handler.py"))
    predict_handler = _load("predict_handler", os.path.join(REPO_ROOT, "predict", "handler.py"))
    ingest_handler.s3 = s3
    predict_handler.s3 = s3

    with open(os.path.join(model_dir, "model.onnx"), "rb") as f:
        model_bytes = f.read()
    with open(os.path.join(model_dir, "norm.json"), "rb") as f:
        norm_bytes = f.read()
    import onnxruntime as ort
    predict_handler._model = ort.InferenceSession(model_bytes, providers=["CPUExecutionProvider"])
    predict_handler._norm = json.loads(norm_bytes)
    print(f"model loaded from {model_dir}/ (use_extra={predict_handler._norm.get('use_extra')}, "
          f"use_dest={predict_handler._norm.get('use_dest')})", flush=True)
    return ingest_handler, predict_handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--model-dir", default="runs/full_B")
    ap.add_argument("--out", default="data/live_test")
    a = ap.parse_args()

    s3 = LocalFileS3(a.out)
    ingest_handler, predict_handler = _stub_aws_and_model(a.model_dir, s3)

    t0 = time.time()
    for minute in range(1, a.minutes + 1):
        loop_start = time.time()
        try:
            states = ingest_handler._fetch_adsb_lol()
        except Exception as exc:  # noqa: BLE001 - one bad minute must not kill a 30-minute run
            print(f"[{minute:>3}/{a.minutes}] live fetch failed: {exc}; skipping this minute", flush=True)
            states = []

        extras = [s.pop("_ml_extras", None) for s in states]
        n_extras = sum(1 for e in extras if e is not None)
        if extras:
            ingest_handler._update_history(extras)

        pred_result: dict[str, Any] = {"predicted": 0, "evaluated": 0, "still_pending": 0}
        try:
            pred_result = predict_handler.handler({}, None)
        except Exception as exc:  # noqa: BLE001
            print(f"[{minute:>3}/{a.minutes}] predict step failed: {exc}", flush=True)

        elapsed_min = (time.time() - t0) / 60
        print(f"[{minute:>3}/{a.minutes}] t={elapsed_min:5.1f}min | live aircraft={len(states):5,} "
              f"(with extras={n_extras:5,}) | predicted={pred_result['predicted']:5,} "
              f"evaluated_this_min={pred_result['evaluated']:3,} pending={pred_result['still_pending']:5,}",
              flush=True)

        if minute < a.minutes:
            sleep_s = max(0.0, 60.0 - (time.time() - loop_start))
            time.sleep(sleep_s)

    metrics = predict_handler._get_json(predict_handler.METRICS_KEY, {"days": []})
    print("\n=== SUMMARY ===")
    if metrics["days"]:
        today = metrics["days"][-1]
        print(f"evaluated predictions today: {today['n']:,} | mean error {today.get('mean_km', '?')} km "
              f"| p90 {today.get('p90_km', '?')} km")
        print("(compare with the offline test set: variant B, all-windows, mean 3.29 km, "
              "median ~1.0 km, p90 ~9.4-10.0 km -- see docs/liveflights-ml-journal.md)")
    else:
        print("no predictions were evaluated yet -- a prediction needs ~10 min of history to be "
              "MADE, then another ~5 min to be evaluated once its target time passes, so a run "
              "shorter than about 15-20 minutes will show 0 here even when everything is working. "
              f"re-run with --minutes 25-30, or inspect {a.out}/live__pending.json directly.")
    print(f"\nall local output is under {a.out}/ (live__history.json, live__predictions.json, "
         "live__pending.json, metrics__daily.json) -- nothing was sent to AWS.")


if __name__ == "__main__":
    main()
