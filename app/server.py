from __future__ import annotations

import asyncio
import sys

import uvicorn
from fastapi import FastAPI

from app.api.routes import router

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


app = FastAPI(
    title="Browser Publish Service",
    description="One-call publish service with automatic per-user cookie acquisition.",
)
app.include_router(router, prefix="/api/v1/publish")

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=19000, workers=1, reload=False)
