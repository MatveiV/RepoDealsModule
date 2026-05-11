"""
FastAPI application factory for REPO Module.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from repo_module.api.routes import router
from repo_module.config import get_config, is_demo
from repo_module.db.base import close_db, init_db
from repo_module.db.base import get_engine
from repo_module.db.orm import register_sqlite_events
from repo_module.utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    cfg = get_config()
    mode = cfg.get("mode", "demo")
    log_level = cfg.get(mode, {}).get("log_level", "INFO")
    log_file = cfg.get(mode, {}).get("log_file") if is_demo() else None

    setup_logging(level=log_level, log_file=log_file)
    logger.info(f"Starting REPO Module in {mode.upper()} mode")

    # Initialize database
    if is_demo():
        engine = get_engine()
        register_sqlite_events(engine)

    await init_db()
    logger.info("Database initialized")

    yield

    await close_db()
    logger.info("REPO Module shutdown complete")


def create_app() -> FastAPI:
    cfg = get_config()
    prefix = cfg.get("api", {}).get("prefix", "/api/v1/repo")

    app = FastAPI(
        title="REPO Module API",
        description="MVP модуля обработки сделок РЕПО",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(router, prefix=prefix, tags=["repo"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": cfg.get("mode", "demo")}

    return app


app = create_app()
