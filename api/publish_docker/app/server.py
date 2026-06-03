"""FastAPI server entrypoint for publish_docker."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.publish_docker.app.api import router
from api.publish_docker.app.worker import worker_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(worker_loop())
    app.state.worker_task = worker_task
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="publish_docker", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
