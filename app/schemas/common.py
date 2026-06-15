"""跨 feature 复用的 HTTP schema 与辅助。

字段都带中文 ``description`` + 可点击调用的 ``examples``；模型级
``json_schema_extra.examples`` 给完整响应示例（Scalar UI 直接点 Send 即可）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.job import (
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_STARTING_REMOTE_LOGIN,
)

# 内部状态里映射到对外 "running"（执行中）的那些。jobs / login-sessions 两个
# mapper 共用。
_RUNNING_STATUSES = {
    STATUS_QUEUED,
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_PUBLISHING,
}


# 登录态失效相关的关键词（与 orchestrator._looks_like_login_required 同源）。
_LOGIN_ERROR_MARKERS = (
    "未登录", "登录", "登陆", "cookie 无效", "cookie无效",
    "cookie 失效", "cookie失效", "login", "auth/page/login",
)


def classify_error(detail: str) -> str:
    """把自由文本的失败原因粗分类成稳定的错误码，供调用方分支处理。"""
    text = (detail or "").lower()
    # 重启清理的失败文案含「登录」/「会话」等词，但既非登录态失效也非远程登录启动失败，
    # 须最先拦下归到通用码，避免被下面的关键词分支误判成 cookie_invalid。
    if "服务已重启" in (detail or ""):
        return "job_failed"
    # 先判远程登录启动失败（其文案里也含「登录」，须排在登录态判断之前）。
    if "远程登录启动失败" in (detail or "") or "remote login" in text or "remote cookie" in text:
        return "remote_login_failed"
    if any(m.lower() in text for m in _LOGIN_ERROR_MARKERS):
        return "cookie_invalid"
    return "job_failed"


# ---------------------------------------------------------------------------
# Service-level
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="存活探针，恒为 ok")
    service: str = Field(default="publish", description="服务名")

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "ok", "service": "publish"}]}
    )


class PlatformInfo(BaseModel):
    id: str = Field(description="平台标识", examples=["toutiao"])
    name: str = Field(description="平台名称", examples=["toutiao"])
    home_url: str = Field(description="平台主页", examples=["https://mp.toutiao.com"])
    login_url: str = Field(
        description="平台登录页", examples=["https://mp.toutiao.com/auth/page/login"]
    )


class PlatformListResponse(BaseModel):
    platforms: list[PlatformInfo]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "platforms": [
                        {
                            "id": "toutiao",
                            "name": "toutiao",
                            "home_url": "https://mp.toutiao.com",
                            "login_url": "https://mp.toutiao.com/auth/page/login",
                        }
                    ]
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# 统一失败信息块
# ---------------------------------------------------------------------------
class ErrorInfo(BaseModel):
    """统一的失败信息块：``code`` 供程序分支，``detail`` 供人排错。仅在任务失败时出现。"""

    code: str = Field(
        description="机器可判断的错误码，如 remote_login_failed | cookie_invalid | job_failed",
        examples=["cookie_invalid"],
    )
    detail: str = Field(default="", description="人类可读的失败原因", examples=["cookie 已失效"])

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"code": "cookie_invalid", "detail": "cookie 已失效"}]}
    )
