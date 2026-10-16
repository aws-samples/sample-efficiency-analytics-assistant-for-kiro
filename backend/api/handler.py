"""Lambda entry point: adapt FastAPI to the API Gateway HTTP API (payload v2) via Mangum."""
from __future__ import annotations

from mangum import Mangum

try:
    from backend.api.main import app
except ImportError:  # flat packaging inside Lambda
    from main import app  # type: ignore

# No ASGI lifespan is needed in Lambda, hence lifespan="off".
handler = Mangum(app, lifespan="off")
