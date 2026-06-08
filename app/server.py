from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.routes import agent, router

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    closed_count = agent.close_stale_running_jobs_after_restart()
    if closed_count:
        print(f"Closed {closed_count} stale running job(s) after service restart.")
    yield


app = FastAPI(
    title="Browser Publish Service",
    description="One-call publish service with automatic per-user cookie acquisition.",
    lifespan=lifespan,
)
app.include_router(router, prefix="/api/v1/publish")

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=19000, workers=1, reload=False)
