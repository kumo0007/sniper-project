"""Run the monitor without the web process.

Start the web app with RUN_WORKER=0 so only one monitor owns the schedule.
A second monitor idles while the first heartbeat is fresh.
"""

from __future__ import annotations

import asyncio

import httpx

from app.db import init_db
from app.main import configure_logging
from app.services.monitor import monitor_loop


async def _main() -> None:
    configure_logging()
    init_db()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        headers={"User-Agent": "MinecraftUsernameMonitor/1.0"},
    ) as http:
        stop = asyncio.Event()
        await monitor_loop(stop, http)


if __name__ == "__main__":
    asyncio.run(_main())
