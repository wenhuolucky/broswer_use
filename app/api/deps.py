"""API 层共享依赖：模块级 agent 单例 + 各 feature 路由复用的辅助函数。"""

from __future__ import annotations

from fastapi import HTTPException

from app.domain.channel import validate_channel_id
from app.domain.job import AutoPublishResponse, Job
from app.publishing.orchestrator import PublishAgent

# 全应用共享的单例：作业/渠道存储、远程登录运行器、作业生命周期都挂在它上面。
agent = PublishAgent()


def raise_for_action_code(resp: AutoPublishResponse) -> None:
    """Translate an agent action result code into a real HTTP error, or pass on success."""
    code = resp.code
    if code in (200, 202):
        return
    if code == 404:
        raise HTTPException(status_code=404, detail=resp.message or "not found")
    if code == 409:
        raise HTTPException(status_code=409, detail=resp.message or "conflict")
    raise HTTPException(status_code=code if code >= 400 else 500, detail=resp.message or "error")


def require_job(job_id: str, expected_type: str, not_found_detail: str) -> Job:
    """按 job_id 取作业并强隔离资源类型。

    发文与登录刻意拆成两套资源：发文是 LLM 自动操作（/jobs），登录是真人手动操作
    （/login-sessions）。两者底层共用同一个 Job 存储（靠 job.type 区分），这里在
    路由层用它强隔离——拿发文 job_id 去敲 /login-sessions/* 会得到 404，反之亦然，
    让两个资源在对外契约上互不可见。"""
    try:
        job = agent.job_store.get(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="查询任务状态失败") from exc
    if job is None or job.type != expected_type:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return job


def require_valid_channel_id(channel_id: str) -> None:
    """按路径取 channel_id 的接口不经过请求体模型校验，这里挡一次非法值，返回干净的
    422 而不是让下游抛 500。"""
    try:
        validate_channel_id(channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
