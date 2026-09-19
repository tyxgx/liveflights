# Airflow image for the liveflights orchestration layer (P8). Built from
# the repo root: docker build -f orchestration/Dockerfile .
#
# BashOperator tasks in orchestration/dags/ never import project code into
# the Airflow process — they shell out via `uv run`, which installs and
# runs the project's own pinned dependency groups (streaming/dbt/ml) in a
# project-local .venv under PROJECT_DIR, completely separate from
# Airflow's own Python environment. This mirrors exactly what `make
# dbt-run` / `make seed` already do on a dev machine, just scheduled.
FROM apache/airflow:2.10.4-python3.12

USER root

# PySpark (gold_batch, compact, ml.anomaly via ml/data.py) needs a JVM.
# The openjdk package path differs by architecture (amd64 vs this
# project's arm64 dev machine, see CLAUDE.md) — symlink to a fixed name so
# JAVA_HOME is one value on either.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jdk-headless \
    && ln -sf "$(dirname "$(dirname "$(readlink -f "$(which javac)")")")" /usr/lib/jvm/default-java \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/default-java
ENV PATH="${JAVA_HOME}/bin:${PATH}"

USER airflow

RUN pip install --no-cache-dir uv

ENV PROJECT_DIR=/opt/liveflights
WORKDIR ${PROJECT_DIR}

# Dependency layer cached separately from the code layer.
COPY --chown=airflow:root pyproject.toml uv.lock ${PROJECT_DIR}/
RUN uv sync --group streaming --group dbt --group ml --no-install-project

COPY --chown=airflow:root . ${PROJECT_DIR}/

# Airflow's own entrypoint expects AIRFLOW_HOME layout; DAGs are mounted
# there (see docker-compose.yml), not copied, so DAG edits don't require a
# rebuild.
WORKDIR /opt/airflow
