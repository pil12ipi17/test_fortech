import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from .config import get_settings
from .db import init_db
from .routers import router as task_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("task-service")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    logger.info("Database initialized")
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
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


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")
