"""平台列表路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.platforms import registry
from app.remote.login import _login_url_for
from app.schemas.common import PlatformInfo, PlatformListResponse

router = APIRouter(tags=["platforms"])


@router.get("/platforms", response_model=PlatformListResponse)
async def list_platforms():
    platforms = [
        PlatformInfo(id=pid, name=cfg.name, home_url=cfg.home_url, login_url=_login_url_for(pid))
        for pid, cfg in registry.configs().items()
    ]
    return PlatformListResponse(platforms=platforms)
