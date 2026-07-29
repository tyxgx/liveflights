"""Configuration for the ML training scripts, loaded from environment/.env."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class MLSettings(BaseSettings):
    """Tunables for the four training scripts. Every field maps 1:1 to a
    variable in .env.example — keep them in sync.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mlflow_tracking_uri: str = "http://localhost:5500"
    mlflow_corridor_experiment: str = "flight-corridors"
    mlflow_trajectory_experiment: str = "trajectory-prediction"
    mlflow_anomaly_experiment: str = "contextual-anomaly-detection"
    mlflow_forecast_experiment: str = "traffic-forecast"

    mlflow_trajectory_model_name: str = "trajectory-predictor"
    mlflow_forecast_model_name: str = "traffic-forecaster"

    database_url: str = "postgresql://liveflights:liveflights@localhost:5433/liveflights"

    # MLflow's S3 artifact store (boto3) reads these from the process
    # environment directly, not from this Settings object — pydantic-settings
    # parsing .env does not itself export anything. Without them, boto3 falls
    # back to ~/.aws/credentials (real AWS) and fails with NoSuchBucket
    # against a bucket that only exists in MinIO. Set as os.environ below so
    # every process that imports ml.config gets this for free, regardless of
    # whether the launching shell happened to export them.
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"

    artifacts_dir: str = "ml/artifacts"
    plots_dir: str = "ml/plots"

    # Minimum valid (t, t+5min) trajectory pairs required before training
    # Model 2 — below this the dataset is too thin to trust a train/test
    # split on, per the explicit instruction not to train on thin data.
    min_trajectory_pairs: int = 2000


settings = MLSettings()

os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", f"http://{settings.minio_endpoint}")
os.environ.setdefault("AWS_ACCESS_KEY_ID", settings.minio_access_key)
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", settings.minio_secret_key)
