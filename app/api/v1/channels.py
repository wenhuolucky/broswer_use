"""渠道路由（/channels）：已连接账号——channel_id ↔ 平台 ↔ 平台账号 ↔ cookie。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import agent, require_valid_channel_id
from app.schemas.channels import (
    ChannelDeleteResponse,
    ChannelPublishStatusResponse,
    ChannelResponse,
    channel_publish_status_from,
    channel_to_response,
)

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: str):
    require_valid_channel_id(channel_id)
    channel = agent.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return channel_to_response(channel)


@router.get("/{channel_id}/publish-status", response_model=ChannelPublishStatusResponse)
async def get_channel_publish_status(channel_id: str):
    require_valid_channel_id(channel_id)
    channel = agent.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    try:
        publish_count = agent.count_unfinished_publish_jobs_for_channel(channel_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="查询任务状态失败") from exc
    return channel_publish_status_from(channel_id, publish_count)


@router.delete("/{channel_id}", response_model=ChannelDeleteResponse)
async def delete_channel(channel_id: str):
    require_valid_channel_id(channel_id)
    deleted = agent.delete_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return ChannelDeleteResponse(channel_id=channel_id, deleted=True)
