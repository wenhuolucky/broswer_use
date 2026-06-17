"""登录会话路由（/login-sessions）：真人在流式浏览器里手动登录，与发文任务隔离。

仍是有状态的异步会话——占用稀缺的 Xvnc 显示槽，故同样需要查询状态、取消。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import agent, raise_for_action_code, require_job
from app.schemas.login_sessions import (
    CreateLoginJobRequest,
    LoginSessionCreatedResponse,
    LoginSessionResponse,
    login_session_created_from,
    login_session_to_response,
)

router = APIRouter(prefix="/login-sessions", tags=["login-sessions"])


@router.post("", response_model=LoginSessionCreatedResponse, status_code=202)
async def create_login_session(request: CreateLoginJobRequest):
    resp = await agent.start_login_only(request)
    return login_session_created_from(resp)


@router.get("/{session_id}", response_model=LoginSessionResponse)
async def get_login_session(session_id: str):
    return login_session_to_response(require_job(session_id, "login", "登录会话不存在"))


@router.delete("/{session_id}", response_model=LoginSessionResponse)
async def cancel_login_session(session_id: str):
    require_job(session_id, "login", "登录会话不存在")
    resp = await agent.cancel_job(session_id)
    raise_for_action_code(resp)
    return login_session_to_response(require_job(session_id, "login", "登录会话不存在"))
