"""发文任务路由（/jobs）：由 LLM 驱动浏览器，长时间执行，故需轮询状态、取消。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import agent, raise_for_action_code, require_job
from app.schemas.jobs import (
    CreatePublishJobRequest,
    JobCreatedResponse,
    JobResponse,
    job_created_from,
    job_to_response,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobCreatedResponse, status_code=202)
async def create_publish_job(request: CreatePublishJobRequest):
    resp = await agent.submit(request)
    if resp.code == 404:
        detail = str((resp.data or {}).get("error_detail") or "") or "渠道不存在，请先登录"
        raise HTTPException(status_code=404, detail=detail)
    return job_created_from(resp, job_type="publish")


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    return job_to_response(require_job(job_id, "publish", "任务不存在"))


@router.post("/{job_id}/save-cookie", response_model=JobResponse)
async def save_cookie(job_id: str):
    # 发文任务在缺 Cookie 时也会走远程登录，故发文资源同样保留 save-cookie。
    require_job(job_id, "publish", "任务不存在")
    resp = await agent.save_remote_cookie(job_id)
    raise_for_action_code(resp)
    return job_to_response(require_job(job_id, "publish", "任务不存在"))


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: str):
    require_job(job_id, "publish", "任务不存在")
    resp = await agent.cancel_job(job_id)
    raise_for_action_code(resp)
    return job_to_response(require_job(job_id, "publish", "任务不存在"))


# 注意：日志是运维/调试关注点，且原始日志会泄漏内部细节（LLM prompt、页面 URL、
# 账号信息等），不属于第三方集成契约。故不对外提供 /jobs/{id}/logs 接口——运维可
# 在服务器侧直接看 logs/jobs/{date}/{job_id}.log 或 grep 统一日志 logs/service.log。
