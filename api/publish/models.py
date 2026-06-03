"""Request and response models for the publish service."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    """Publish request payload."""

    title: str = Field(..., min_length=1, max_length=200, description="文章标题")
    content: str = Field(..., min_length=1, description="文章正文内容，支持普通文本或 Markdown")
    cookie: str = Field(..., description="头条号登录 cookie（JSON 字符串或原始 cookie 字符串）")
    cover_image_path: Optional[str] = Field(None, description="封面图片本地文件路径（绝对路径）")


class PublishContent(BaseModel):
    """Inner response payload."""

    message: str = Field(..., description="发布结果消息")
    url: str = Field(default="", description="发布成功后的文章链接")
    user_name: str = Field(default="", description="发布账号名称")
    user_id: str = Field(default="", description="发布账号 ID")
    operation_time: str = Field(default="", description="操作时间")
    article_title: str = Field(default="", description="文章标题")
    elapsed_seconds: float = Field(default=0.0, description="总耗时（秒）")
    llm_usage: dict = Field(default_factory=dict, description="LLM token 消耗统计")
    log_file_path: str = Field(default="", description="本次请求的日志文件路径")
    receipt: dict = Field(default_factory=dict, description="完整回执信息")


class PublishResponse(BaseModel):
    """Outer response payload."""

    code: int = Field(default=200, description="状态码，成功 200，失败 500")
    request_id: str = Field(..., description="全局唯一请求 ID")
    content: PublishContent = Field(..., description="发布结果详情")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "request_id": self.request_id,
            "content": self.content.model_dump(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def make_success_response(
    request_id: str,
    message: str = "发布成功",
    url: str = "",
    user_name: str = "",
    user_id: str = "",
    operation_time: str = "",
    article_title: str = "",
    elapsed_seconds: float = 0.0,
    llm_usage: dict = None,
    log_file_path: str = "",
    receipt: dict = None,
) -> PublishResponse:
    return PublishResponse(
        code=200,
        request_id=request_id,
        content=PublishContent(
            message=message,
            url=url,
            user_name=user_name,
            user_id=user_id,
            operation_time=operation_time,
            article_title=article_title,
            elapsed_seconds=elapsed_seconds,
            llm_usage=llm_usage or {},
            log_file_path=log_file_path,
            receipt=receipt or {},
        ),
    )


def make_error_response(
    request_id: str,
    message: str = "发布失败",
    code: int = 500,
    url: str = "",
    user_name: str = "",
    user_id: str = "",
    operation_time: str = "",
    article_title: str = "",
    elapsed_seconds: float = 0.0,
    llm_usage: dict = None,
    log_file_path: str = "",
    receipt: dict = None,
) -> PublishResponse:
    return PublishResponse(
        code=code,
        request_id=request_id,
        content=PublishContent(
            message=message,
            url=url,
            user_name=user_name,
            user_id=user_id,
            operation_time=operation_time,
            article_title=article_title,
            elapsed_seconds=elapsed_seconds,
            llm_usage=llm_usage or {},
            log_file_path=log_file_path,
            receipt=receipt or {},
        ),
    )
