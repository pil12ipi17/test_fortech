import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from shared.errors import register_error_handlers
from shared.metrics import register_prometheus_metrics
from shared.tracing import register_tracing_middleware

from .core.config import get_settings
from .core.db import engine, run_migrations
from .api.routers import router as task_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("task-service")


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations()
    logger.info("Database migrations applied")
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
register_error_handlers(app)
register_tracing_middleware(app, service_name=settings.app_name, otlp_endpoint=settings.otel_exporter_otlp_endpoint)
register_prometheus_metrics(app, service_name=settings.app_name)
app.include_router(task_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info("%s %s -> %s %.2fms", request.method, request.url.path, response.status_code, duration_ms)
    return response


@app.get("/health", tags=["health"])
def healthcheck():
    return {"status": "ok", "service": settings.app_name}


@app.get("/readiness", tags=["health"])
def readiness():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is not ready") from exc
    return {"status": "ok", "service": settings.app_name, "database": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")
