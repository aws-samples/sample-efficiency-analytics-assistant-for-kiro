"""FastAPI dependencies: query builder and query layer.

- Production: QueryLayer uses the real Athena runner.
- Tests: inject a mock QueryLayer via app.dependency_overrides.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

try:
    from backend.shared.athena_client import Queries, QueryLayer, make_athena_runner
except ImportError:  # flat packaging inside Lambda
    from shared.athena_client import Queries, QueryLayer, make_athena_runner  # type: ignore

try:
    from backend.api.settings import Settings, get_settings
except ImportError:
    from settings import Settings, get_settings  # type: ignore


@lru_cache(maxsize=1)
def _real_query_layer() -> QueryLayer:
    s = get_settings()
    runner = make_athena_runner(
        database=s.glue_db, workgroup=s.athena_workgroup, region=s.region)
    return QueryLayer(runner, cache_ttl=s.cache_ttl)


def get_queries(settings: Settings = Depends(get_settings)) -> Queries:
    return Queries(db=settings.glue_db)


def get_query_layer() -> QueryLayer:
    """Returns the real query layer by default; tests override it via dependency_overrides."""
    return _real_query_layer()
