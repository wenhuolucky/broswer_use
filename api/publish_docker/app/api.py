"""Minimal API for publish_docker tests."""

import tempfile

from fastapi import APIRouter, Form
from fastapi import File, UploadFile
from fastapi.responses import JSONResponse

from api.publish_docker.app.queue_store import store
from api.publish_docker.app.models import JobDetailResponse

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "worker_state": store.worker_state,
        "queue_size": store.queue.qsize(),
        "version": "phase1",
    }


@router.post("/jobs/publish", status_code=202)
async def publish(
    title: str = Form(...),
    content: str = Form(...),
    cookie_text: str | None = Form(default=None),
    cookie_file: UploadFile | None = File(default=None),
    cover_image: UploadFile | None = File(default=None),
    cover_image_url: str | None = Form(default=None),
):
    if not store.can_accept():
        return JSONResponse(
            status_code=503,
            content={"code": 503, "message": "queue is full"},
        )

    normalized_cookie = cookie_text.strip() if cookie_text and cookie_text.strip() else None
    if not normalized_cookie and cookie_file:
        normalized_cookie = (await cookie_file.read()).decode("utf-8", errors="replace").strip()

    if not normalized_cookie:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "cookie is required"},
        )

    cover_image_path = None
    if cover_image:
        suffix = ""
        if cover_image.filename and "." in cover_image.filename:
            suffix = "." + cover_image.filename.rsplit(".", 1)[-1]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="publish_docker_cover_")
        try:
            tmp.write(await cover_image.read())
        finally:
            tmp.close()
        cover_image_path = tmp.name

    job = store.create_job(
        title=title,
        content=content,
        cookie_text=normalized_cookie,
        cover_image_path=cover_image_path,
        cover_image_url=cover_image_url,
    )
    return JSONResponse(
        status_code=202,
        content={
            "code": 202,
            "job_id": job.job_id,
            "status": job.status,
            "message": "job accepted",
        },
    )


def _build_job_response(job) -> dict:
    """将 PublishJob 映射为双层嵌套响应格式。"""
    if job.status in ("queued", "running"):
        return {
            "context": {
                "id": job.job_id,
                "code": 202,
                "reason": "",
            },
            "data": {
                "user": "",
                "url": "",
                "title": job.title,
            },
        }

    if job.status == "succeeded":
        account = job.result.get("account", "") or job.result.get("account_name", "") or ""
        article_url = job.result.get("article_url", "") or ""
        return {
            "context": {
                "id": job.job_id,
                "code": 200,
                "reason": "",
            },
            "data": {
                "user": account,
                "url": article_url,
                "title": job.title,
            },
        }

    # failed
    reason = job.error or job.result.get("failure_reason", "") or "未知错误"
    account = job.result.get("account", "") or job.result.get("account_name", "") or ""
    article_url = job.result.get("article_url", "") or ""
    return {
        "context": {
            "id": job.job_id,
            "code": 500,
            "reason": reason,
        },
        "data": {
            "user": account,
            "url": article_url,
            "title": job.title,
        },
    }


@router.get("/jobs/{job_id}", response_model=JobDetailResponse, summary="查询发布任务结果")
async def get_job(job_id: str):
    job = store.get_job(job_id)
    if not job:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "job not found"},
        )

    return _build_job_response(job)
