"""发文任务路由（/jobs）：由 LLM 驱动浏览器，长时间执行，故需轮询状态、取消。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.api.deps import agent, raise_for_action_code, require_job
from app.schemas.jobs import (
    CreatePublishJobRequest,
    JobCreatedResponse,
    JobResponse,
    job_created_from,
    job_to_response,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=202,
    summary="创建发文任务",
    description=(
        "提交一篇文章到指定 channel_id。接口会快速返回发文任务 ID，实际发文异步执行；"
        "调用方应继续轮询 GET /api/v1/jobs/{job_id} 查看状态和 article_url。"
    ),
)
async def create_publish_job(request: CreatePublishJobRequest):
    resp = await agent.submit(request)
    if resp.code == 404:
        detail = str((resp.data or {}).get("error_detail") or "") or "渠道不存在，请先登录"
        raise HTTPException(status_code=404, detail=detail)
    return job_created_from(resp)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="查询发文任务",
    description=(
        "查询发文任务当前状态、远程浏览器地址、失败原因和成功后的文章链接。"
        "只接受发文任务 ID；登录会话 ID 会返回 404。"
    ),
)
async def get_job(job_id: str = Path(description="发文任务 ID，由 POST /api/v1/jobs 返回。")):
    return job_to_response(require_job(job_id, "publish", "任务不存在"))


@router.post(
    "/{job_id}/save-cookie",
    response_model=JobResponse,
    summary="保存发文任务 Cookie",
    description=(
        "当发文任务进入 waiting_cookie 并要求远程登录时，在用户完成登录后调用本接口保存 cookie 并续发。"
        "只适用于发文任务，不适用于独立登录会话。"
    ),
)
async def save_cookie(job_id: str = Path(description="发文任务 ID，由 POST /api/v1/jobs 返回。")):
    # 发文任务在缺 Cookie 时也会走远程登录，故发文资源同样保留 save-cookie。
    require_job(job_id, "publish", "任务不存在")
    resp = await agent.save_remote_cookie(job_id)
    raise_for_action_code(resp)
    return job_to_response(require_job(job_id, "publish", "任务不存在"))


@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    summary="取消发文任务",
    description=(
        "取消指定发文任务。若任务已成功或已失败，返回的状态以当前任务实际状态为准。"
        "只接受发文任务 ID；登录会话 ID 会返回 404。"
    ),
)
async def cancel_job(job_id: str = Path(description="发文任务 ID，由 POST /api/v1/jobs 返回。")):
    require_job(job_id, "publish", "任务不存在")
    resp = await agent.cancel_job(job_id)
    raise_for_action_code(resp)
    return job_to_response(require_job(job_id, "publish", "任务不存在"))


# 注意：日志是运维/调试关注点，且原始日志会泄漏内部细节（LLM prompt、页面 URL、
# 账号信息等），不属于第三方集成契约。故不对外提供 /jobs/{id}/logs 接口——运维可
# 在服务器侧直接看 logs/jobs/{date}/{job_id}.log 或 grep 统一日志 logs/service.log。
