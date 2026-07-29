"""Postgres access: a single shared SQLAlchemy engine + a thin query helper.

Plain synchronous psycopg2-backed SQLAlchemy is used deliberately over
async drivers here — at this data volume (gold aggregates, low hundreds to
low thousands of rows) the added complexity of asyncpg buys nothing, and
FastAPI runs sync route bodies in a threadpool automatically.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from api.config import settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5)
    return _engine


def query_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a read query, return a DataFrame. Small helper, not an ORM."""
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def check_connection() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
