"""FastAPI route definitions for the publish service."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile, Request

from api.publish.logger_config import setup_request_logger
from api.publish.middleware import request_id_var
from api.publish.models import make_error_response, make_success_response
from api.publish.publish_service import PublishService

router = APIRouter()
publish_service = PublishService()


@router.post(
    "/publish",
    summary="发布文章",
    description="""
## 一键发布文章到头条号

填写表单后即可触发浏览器自动化发布流程。

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| **标题** | 是 | 文章标题 |
| **正文** | 是 | 文章正文，支持普通文本或 Markdown |
| **登录 Cookie 文件** | 是 | 上传 JSON 格式 cookie 文件，或直接粘贴 cookie JSON 文本 |
| **封面图片** | 否 | 上传一张封面图片作为文章封面 |

### 正文图片规则

- Markdown 正文里的图片只支持网络 URL。
- 服务会把 Markdown 直接转换成 HTML 富文本，再粘贴进头条编辑器。
- 不再支持单独上传正文图片文件。
    """,
    response_description="发布结果",
    tags=["文章发布"],
)
async def publish_article(
    request: Request,
    title: str = Form(..., description="文章标题"),
    content: str = Form(..., description="文章正文内容，支持普通文本或 Markdown"),
    cookie_file: Optional[UploadFile] = File(
        None,
        description="上传登录 Cookie 文件（JSON 格式，如 auth.json），与 cookie_text 二选一",
    ),
    cookie_text: Optional[str] = Form(
        None,
        description="直接粘贴 Cookie JSON 文本，与 cookie_file 二选一",
    ),
    cover_image: Optional[UploadFile] = File(None, description="封面图片（可选）"),
    cover_image_url: Optional[str] = Form(
        None,
        description="封面图片 URL（可选，与 cover_image 二选一，优先使用本地上传）",
    ),
):
    request_id = request_id_var.get()

    cookie = None
    cookie_file_path = None
    if cookie_file:
        cookie_file_path = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".json",
            prefix=f"cookie_{request_id[:8]}_",
        ).name
        file_content = await cookie_file.read()
        with open(cookie_file_path, "wb") as file_obj:
            file_obj.write(file_content)
        try:
            with open(cookie_file_path, "r", encoding="utf-8") as file_obj:
                cookie_data = json.load(file_obj)
            cookie = json.dumps(cookie_data, ensure_ascii=False)
        except json.JSONDecodeError:
            cookie = file_content.decode("utf-8", errors="replace")
    elif cookie_text:
        cookie = cookie_text.strip()
    else:
        logger = setup_request_logger(request_id)
        logger.error("[API] 未提供 cookie 文件或文本")
        return make_error_response(
            request_id=request_id,
            message="请上传 cookie 文件或粘贴 cookie JSON 文本",
            code=400,
        ).to_dict()

    logger = setup_request_logger(request_id)

    cover_path = None
    if cover_image:
        suffix = os.path.splitext(cover_image.filename)[1] if cover_image.filename else ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix=f"cover_{request_id[:8]}_")
        tmp.close()
        file_content = await cover_image.read()
        with open(tmp.name, "wb") as file_obj:
            file_obj.write(file_content)
        cover_path = tmp.name
        logger.info(f"[API] 封面图片（本地）: {cover_path}")
    elif cover_image_url:
        try:
            import httpx
            import mimetypes

            with httpx.Client(timeout=30) as client:
                response = client.get(cover_image_url)
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    ext = mimetypes.guess_extension(content_type) or ".jpg"
                    tmp = tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=ext,
                        prefix=f"cover_{request_id[:8]}_",
                    )
                    tmp.close()
                    with open(tmp.name, "wb") as file_obj:
                        file_obj.write(response.content)
                    cover_path = tmp.name
                    logger.info(f"[API] 封面图片（URL 下载）: {cover_path}")
                else:
                    logger.warning(f"[API] 封面图片下载失败: HTTP {response.status_code}")
        except Exception as exc:
            logger.warning(f"[API] 封面图片下载失败: {exc}")

    try:
        result = await publish_service.publish(
            title=title,
            content=content,
            cookie=cookie,
            request_id=request_id,
            cover_image_path=cover_path,
        )

        if cookie_file_path:
            try:
                os.remove(cookie_file_path)
            except OSError:
                pass

        if cover_path:
            try:
                os.remove(cover_path)
            except OSError:
                pass

        elapsed = result.get("elapsed_seconds", 0.0)
        llm_usage = result.get("llm_usage", {})
        log_file_path = f"logs/requests/{request_id}.log"
        receipt = result.get("receipt", {})

        if result.get("success"):
            token_msg = ""
            if llm_usage:
                token_msg = f"，LLM 消耗 {llm_usage.get('total_tokens', 0)} tokens"
            response = make_success_response(
                request_id=request_id,
                message=f"发布成功（耗时 {elapsed:.1f}s{token_msg}）",
                url=result.get("article_url", ""),
                user_name=result.get("user_name", result.get("account", "")),
                user_id=result.get("user_id", ""),
                operation_time=result.get("operation_time", ""),
                article_title=result.get("article_title", ""),
                elapsed_seconds=elapsed,
                llm_usage=llm_usage,
                log_file_path=log_file_path,
                receipt=receipt,
            )
        else:
            response = make_error_response(
                request_id=request_id,
                message=result.get("failure_reason", "发布失败"),
                code=500,
                url=result.get("article_url", ""),
                user_name=result.get("user_name", result.get("account", "")),
                user_id=result.get("user_id", ""),
                operation_time=result.get("operation_time", ""),
                article_title=result.get("article_title", ""),
                elapsed_seconds=elapsed,
                llm_usage=llm_usage,
                log_file_path=log_file_path,
                receipt=receipt,
            )

        return response.to_dict()
    except Exception as exc:
        logger.error(f"[API] 未捕获异常: {exc}", exc_info=True)

        if cookie_file_path:
            try:
                os.remove(cookie_file_path)
            except OSError:
                pass

        if cover_path:
            try:
                os.remove(cover_path)
            except OSError:
                pass

        return make_error_response(
            request_id=request_id,
            message=f"服务异常: {str(exc)}",
            code=500,
            elapsed_seconds=0.0,
            log_file_path=f"logs/requests/{request_id}.log",
            receipt={"error": str(exc), "success": False},
        ).to_dict()


@router.get(
    "/health",
    summary="服务状态检查",
    description="检查发布服务是否正常运行。",
    tags=["系统"],
)
async def health_check():
    return {"status": "运行中", "version": "1.0.0", "提示": "返回运行中表示服务可用"}
