from __future__ import annotations

import asyncio
import sys

from fastapi import FastAPI

from auto.api import router

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


app = FastAPI(
    title="Auto Publish Service",
    description="One-call publish service with automatic per-user cookie acquisition.",
)
app.include_router(router, prefix="/api/v1/auto")
