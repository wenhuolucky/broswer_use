from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auto.models import AutoPublishRequest, CompleteCookieRequest
from auto.publish_agent import PublishAgent


router = APIRouter()
agent = PublishAgent()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "auto"}


@router.post("/publish")
async def publish(request: AutoPublishRequest):
    return (await agent.submit(request)).model_dump()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = agent.job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "payload": job.payload,
        "login_url": job.login_url,
        "remote_session_id": job.remote_session_id,
        "log_file_path": job.log_file_path,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@router.post("/jobs/{job_id}/cookies")
async def complete_cookies(job_id: str, request: CompleteCookieRequest):
    return (await agent.resume_after_cookie(job_id, request.cookies)).model_dump()
