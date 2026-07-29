"""Redis-backed response caching for the stats endpoints (60s TTL).

Wraps a plain data-fetching function; returns (data, cache_hit) so routers
can surface hit/miss via an `X-Cache` response header rather than polluting
the JSON payload itself.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging

import redis

from api.config import settings

logger = logging.getLogger("api.cache")

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
            socket_connect_timeout=2,
        )
    return _client


def check_connection() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False


def _cache_key(prefix: str, *args, **kwargs) -> str:
    raw = f"{prefix}:{args}:{sorted(kwargs.items())}"
    return f"liveflights:{prefix}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def cached(prefix: str, ttl: int | None = None):
    """Decorator: wraps a data-fetching function so it returns
    `(result, cache_hit: bool)`. On a Redis outage, falls back to always
    computing fresh (fails open, never breaks the endpoint).
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = _cache_key(prefix, *args, **kwargs)
            try:
                cached_value = get_redis().get(key)
            except Exception as exc:
                logger.warning("Redis unavailable, bypassing cache: %s", exc)
                return func(*args, **kwargs), False

            if cached_value is not None:
                return json.loads(cached_value), True

            result = func(*args, **kwargs)
            try:
                get_redis().setex(
                    key, ttl or settings.redis_cache_ttl_seconds, json.dumps(result, default=str)
                )
            except Exception as exc:
                logger.warning("Redis unavailable, skipping cache write: %s", exc)
            return result, False

        return wrapper

    return decorator
