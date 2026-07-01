"""登录会话路由（/login-sessions）：真人在流式浏览器里手动登录，与发文任务隔离。

仍是有状态的异步会话——占用稀缺的 Xvnc 显示槽，故同样需要查询状态、取消。
"""

from __future__ import annotations

from fastapi import APIRouter, Path

from app.api.deps import agent, raise_for_action_code, require_job
from app.schemas.login_sessions import (
    CreateLoginJobRequest,
    LoginSessionCreatedResponse,
    LoginSessionResponse,
    login_session_created_from,
    login_session_to_response,
)

router = APIRouter(prefix="/login-sessions", tags=["login-sessions"])


@router.post(
    "",
    response_model=LoginSessionCreatedResponse,
    status_code=202,
    summary="创建登录会话",
    description=(
        "为指定平台创建一个真人手动登录会话，并签发 channel_id。"
        "用户应打开 live_url 完成登录；登录成功后 channel_id 可用于账号绑定和后续发文。"
    ),
)
async def create_login_session(request: CreateLoginJobRequest):
    resp = await agent.start_login_only(request)
    return login_session_created_from(resp)


@router.get(
    "/{session_id}",
    response_model=LoginSessionResponse,
    summary="查询登录会话",
    description=(
        "查询独立登录会话的状态、channel_id 和远程登录地址。"
        "只接受登录会话 ID；发文任务 ID 会返回 404。"
    ),
)
async def get_login_session(session_id: str = Path(description="登录会话 ID，由 POST /api/v1/login-sessions 返回。")):
    return login_session_to_response(require_job(session_id, "login", "登录会话不存在"))


@router.delete(
    "/{session_id}",
    response_model=LoginSessionResponse,
    summary="取消登录会话",
    description=(
        "取消指定登录会话并释放远程浏览器资源。"
        "只接受登录会话 ID；发文任务 ID 会返回 404。"
    ),
)
async def cancel_login_session(session_id: str = Path(description="登录会话 ID，由 POST /api/v1/login-sessions 返回。")):
    require_job(session_id, "login", "登录会话不存在")
    resp = await agent.cancel_job(session_id)
    raise_for_action_code(resp)
    return login_session_to_response(require_job(session_id, "login", "登录会话不存在"))
