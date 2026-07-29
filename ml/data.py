"""Shared silver-layer data loading for the ML training scripts.

Silver lives as a Delta table on MinIO (not Postgres — only gold's
aggregates are mirrored there), so we reuse the same Spark session helper
streaming jobs use. We deliberately avoid `DataFrame.toPandas()`: PySpark
3.5.3's pandas-conversion path imports `distutils`, which Python 3.12
removed outright (PEP 632) — `toPandas()` raises `ModuleNotFoundError` on
this interpreter. Writing to a local Parquet file and reading it back with
pandas/pyarrow sidesteps that broken code path entirely and is just as fast
for the data volumes here.
"""

from __future__ import annotations

import shutil
import tempfile

import pandas as pd

from streaming.config import settings as streaming_settings
from streaming.session import get_spark_session


def load_silver(app_name: str) -> pd.DataFrame:
    """Load the full silver Delta table as a pandas DataFrame."""
    spark = get_spark_session(app_name)
    spark.sparkContext.setLogLevel("WARN")
    tmp_dir = tempfile.mkdtemp(prefix="silver_export_")
    try:
        sdf = spark.read.format("delta").load(streaming_settings.lake_path("silver"))
        sdf.write.mode("overwrite").parquet(tmp_dir)
        pdf = pd.read_parquet(tmp_dir)
    finally:
        spark.stop()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    pdf["ingest_ts"] = pd.to_datetime(pdf["ingest_ts"], utc=True)
    return pdf
