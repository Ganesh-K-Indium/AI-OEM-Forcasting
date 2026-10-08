from __future__ import annotations

import logging
import time

from contextlib import asynccontextmanager

import anyio.to_thread

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api import admin, auth, forecast, governance, mapping, risk  # noqa: F401
from app.core.config import get_settings
from app.core import db as _db
from app.core.db import Base, SessionLocal, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("oem")
_METRICS = {"requests": 0, "errors": 0, "latency_sum": 0.0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await anyio.to_thread.run_sync(_bootstrap, s)
    log.info("startup complete (env=%s, synthetic=%s)", s.environment, s.synthetic_mode)
    yield
    await _db.async_engine.dispose()


def _bootstrap(s) -> None:
    if s.environment != "prod" or s.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)  # dev/test convenience; production uses `alembic upgrade head`
    with SessionLocal() as db:
        from app.core.settings_store import seed_defaults
        from app.mapping.rules import seed_default_rules
        from app.risk.engine import seed_default_thresholds

        seed_defaults(db)
        seed_default_rules(db)
        seed_default_thresholds(db)
        if s.bootstrap_demo_users:
            from app.data.loader import ensure_demo_users

            ensure_demo_users(db)
        db.commit()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="1.0.0", lifespan=lifespan, description="Units -> ASP -> revenue forecasting with CRM uplift, MinT reconciliation, governed overrides and risk alerts.")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def metrics(request: Request, call_next):
        t0 = time.time()
        try:
            resp = await call_next(request)
        except Exception:
            _METRICS["errors"] += 1
            raise
        _METRICS["requests"] += 1
        _METRICS["latency_sum"] += time.time() - t0
        if resp.status_code >= 500:
            _METRICS["errors"] += 1
        return resp

    p = "/api/v1"
    app.include_router(forecast.public, prefix=p)
    for r in (auth.router, forecast.router, governance.router, risk.router, mapping.router, admin.router):
        app.include_router(r, prefix=p)

    @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    def prom():
        n = max(_METRICS["requests"], 1)
        return (f"oem_http_requests_total {_METRICS['requests']}\noem_http_errors_total {_METRICS['errors']}\n"
                f"oem_http_latency_seconds_avg {_METRICS['latency_sum'] / n:.6f}\n")

    return app


app = create_app()
