"""FastAPI application entrypoint for the publish service."""

from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html

from publish_service.config import HOST, PORT, SERVICE_VERSION
from publish_service.logger_config import get_service_logger
from publish_service.middleware import RequestIDMiddleware, request_id_var
from publish_service.service_api import router

app = FastAPI(
    title="头条号文章发布服务",
    description="将头条号自动发文功能封装成 HTTP 服务，支持通过网页直接发布文章。",
    version=SERVICE_VERSION,
    docs_url=None,
)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="头条号发布服务 - 控制台",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        swagger_ui_parameters={
            "defaultModelsExpandDepth": -1,
            "displayRequestDuration": True,
            "filter": True,
            "docExpansion": "list",
            "persistAuthorization": True,
            "withCredentials": True,
            "syntaxHighlight.theme": "obsidian",
            "tryItOutEnabled": True,
            "requestSnippets.enabled": False,
            "defaultModelRendering": "example",
        },
        oauth2_redirect_url=None,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.include_router(router, prefix="/api/v1")


@app.exception_handler(422)
async def validation_exception_handler(request, exc):
    from fastapi.responses import JSONResponse

    rid = request_id_var.get() or "N/A"
    errors = exc.errors() if hasattr(exc, "errors") else []
    field_map = {
        "title": "标题",
        "content": "正文",
        "cookie_file": "Cookie 文件",
        "cookie_text": "Cookie 文本",
        "cover_image": "封面图片",
        "cover_image_url": "封面图片 URL",
    }

    messages = []
    for err in errors:
        loc = err.get("loc", [])
        msg = err.get("msg", "")
        field = loc[-1] if loc else "未知字段"
        field_name = field_map.get(field, field)
        messages.append(f"{field_name}: {msg}")

    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "request_id": rid,
            "content": {
                "message": "参数校验失败: " + "; ".join(messages),
                "url": "",
                "user_name": "",
                "user_id": "",
                "operation_time": "",
                "article_title": "",
            },
        },
    )


@app.on_event("startup")
async def startup_event():
    logger = get_service_logger()
    logger.info("=" * 60)
    logger.info("头条号发布服务启动中...")
    logger.info(f"版本: {SERVICE_VERSION}")
    logger.info(f"监听地址: {HOST}:{PORT}")
    logger.info(f"访问控制台: http://{HOST}:{PORT}/docs")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    logger = get_service_logger()
    logger.info("头条号发布服务关闭中...")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "publish_service.server:app",
        host=HOST,
        port=PORT,
        reload="--reload" in sys.argv,
        log_level="info",
    )
