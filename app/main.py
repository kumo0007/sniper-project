from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, get_settings
from app.db import init_db
from app.routes.api import router
from app.services.accounts import cancel_login_tasks, resume_pending_logins
from app.services.monitor import monitor_loop
from app.services.sanitize import sanitize

logger = logging.getLogger("sniper")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = sanitize(record.getMessage())
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redact = _RedactFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        init_db()
        http = httpx.AsyncClient(timeout=httpx.Timeout(20.0), headers={"User-Agent": "MinecraftUsernameMonitor/1.0"})
        app.state.http = http
        resume_pending_logins(http)
        stop = None
        task = None
        if settings.run_worker:
            import asyncio

            stop = asyncio.Event()
            task = asyncio.create_task(monitor_loop(stop, http))
        logger.info("Web app ready on %s:%s", settings.host, settings.port)
        try:
            yield
        finally:
            if stop is not None and task is not None:
                stop.set()
                await task
            await cancel_login_tasks()
            await http.aclose()

    app = FastAPI(title="Minecraft Username Monitor", lifespan=lifespan)
    app.include_router(router)
    static_dir = ROOT / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
