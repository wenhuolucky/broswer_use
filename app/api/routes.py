from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import require_api_token
from app.jobs.models import AutoPublishRequest, LoginRequest
from app.jobs.models import (
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
from app.publishing.agent import PublishAgent
from app.utils.urls import normalize_toutiao_article_url


router = APIRouter()
agent = PublishAgent()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "publish"}


@router.post("/publish")
async def publish(request: AutoPublishRequest, auth_error: dict | None = Depends(require_api_token)):
    if auth_error:
        return auth_error
    return (await agent.submit(request)).model_dump()


@router.post("/login")
async def login(request: LoginRequest, auth_error: dict | None = Depends(require_api_token)):
    if auth_error:
        return auth_error
    return (await agent.start_login_only(request)).model_dump()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, auth_error: dict | None = Depends(require_api_token)):
    if auth_error:
        return auth_error
    try:
        job = agent.job_store.get(job_id)
    except Exception:
        return _job_response(
            code=503,
            task_status="query_failed",
            message="查询任务状态失败",
            job_id=job_id,
        )

    if not job:
        return _job_response(
            code=404,
            task_status="not_found",
            message="任务不存在",
            job_id=job_id,
        )
    return _format_job_response(job)


@router.post("/savecookie/{job_id}")
async def save_cookie(job_id: str, auth_error: dict | None = Depends(require_api_token)):
    if auth_error:
        return auth_error
    return (await agent.save_remote_cookie(job_id)).model_dump()


def _format_job_response(job):
    status = job.status
    if status in {
        STATUS_QUEUED,
        STATUS_CHECKING_COOKIE,
        STATUS_COOKIE_READY,
        STATUS_STARTING_REMOTE_LOGIN,
        STATUS_PUBLISHING,
    }:
        return _job_response(
            code=202,
            task_status="running",
            message="发布任务正在后台执行",
            job_id=job.job_id,
        )
    if status == STATUS_WAITING_COOKIE:
        return _job_response(
            code=401,
            task_status="login_required",
            message="需要用户登录或 Cookie 已失效",
            job_id=job.job_id,
            extra_data={"login_url": job.login_url},
        )
    if status == STATUS_SUCCEEDED:
        if (job.payload or {}).get("job_type") == "login_only":
            payload = job.payload or {}
            result = job.result or {}
            return _job_response(
                code=200,
                task_status="login_succeeded",
                message="登录 Cookie 保存成功",
                job_id=job.job_id,
                extra_data={
                    "user_id": str(payload.get("user_id", "") or ""),
                    "platform": str(payload.get("platform", "") or ""),
                    "cookie_ready": bool(result.get("cookie_ready", False)),
                },
            )
        data = _published_job_data(job)
        article_url = data["article_url"]
        return _job_response(
            code=200,
            task_status="published",
            message="文章发布成功" if article_url else "文章发布成功，但未获取到文章链接",
            job_id=job.job_id,
            extra_data=data,
        )
    if status == "expired":
        return _job_response(
            code=408,
            task_status="expired",
            message="浏览器 session 已过期",
            job_id=job.job_id,
        )
    if status == "closed":
        return _job_response(
            code=410,
            task_status="closed",
            message="浏览器 session 已关闭",
            job_id=job.job_id,
        )
    reason = job.error or (job.result or {}).get("failure_reason", "") or (job.result or {}).get("message", "")
    return _job_response(
        code=500,
        task_status="failed",
        message="发布失败",
        job_id=job.job_id,
        extra_data={"reason": reason},
    )


def _job_response(code: int, task_status: str, message: str, job_id: str, extra_data: dict | None = None):
    data = {"job_id": job_id}
    if extra_data:
        data.update(extra_data)
    return {
        "code": code,
        "task_status": task_status,
        "message": message,
        "data": data,
    }


def _published_job_data(job) -> dict:
    payload = job.payload or {}
    result = job.result or {}
    return {
        "user_id": str(payload.get("user_id", "") or ""),
        "platform": str(payload.get("platform", "") or ""),
        "title": str(payload.get("title", "") or ""),
        "cover_image_url": str(payload.get("cover_image_url", "") or ""),
        "article_url": normalize_toutiao_article_url(str(result.get("article_url", "") or "")),
        "publish_result": {
            "success": bool(result.get("success", False)),
            "account_name": str(result.get("account_name", "") or result.get("account", "") or ""),
            "platform_user_id": str(result.get("user_id", "") or ""),
            "article_title": str(result.get("article_title", "") or ""),
            "publish_signal": str(result.get("publish_signal", "") or ""),
            "operation_time": str(result.get("operation_time", "") or ""),
        },
    }
