"""v1 路由聚合：把各 feature 的受保护 router 汇总成一个 api_router。

唯一的公开（不鉴权）路由 /health 在 server.py 顶层定义（不带 /api/v1 前缀）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import accounts, channels, jobs, login_sessions, platforms

api_router = APIRouter()
for module in (platforms, jobs, login_sessions, channels, accounts):
    api_router.include_router(module.router)
