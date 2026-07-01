"""渠道路由（/channels）：已连接账号——channel_id ↔ 平台 ↔ 平台账号 ↔ cookie。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.api.deps import agent, require_valid_channel_id
from app.schemas.channels import (
    ChannelDeleteResponse,
    ChannelPublishStatusResponse,
    ChannelResponse,
    channel_publish_status_from,
    channel_to_response,
)

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get(
    "/{channel_id}",
    response_model=ChannelResponse,
    summary="查询渠道详情",
    description=(
        "查询 channel_id 对应的已连接平台账号，包括平台、账号名、channel 状态以及 cookie 是否可用。"
        "channel 由登录会话签发，也会被账号管理接口引用。"
    ),
)
async def get_channel(channel_id: str = Path(description="渠道 ID，由登录会话签发，用于账号绑定和发文。")):
    require_valid_channel_id(channel_id)
    channel = agent.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return channel_to_response(channel)


@router.get(
    "/{channel_id}/publish-status",
    response_model=ChannelPublishStatusResponse,
    summary="查询渠道发文状态",
    description=(
        "实时查询指定 channel 当前是否空闲，以及该 channel 下未完成 publish job 数量。"
        "这是运行时状态，不会写入账号表。"
    ),
)
async def get_channel_publish_status(
    channel_id: str = Path(description="渠道 ID，由登录会话签发，用于账号绑定和发文。"),
):
    require_valid_channel_id(channel_id)
    channel = agent.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    try:
        publish_count = agent.count_unfinished_publish_jobs_for_channel(channel_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="查询任务状态失败") from exc
    return channel_publish_status_from(channel_id, publish_count)


@router.delete(
    "/{channel_id}",
    response_model=ChannelDeleteResponse,
    summary="删除渠道",
    description=(
        "删除指定 channel 及其 cookie。若该 channel 已绑定账号，通常应优先使用账号删除接口，"
        "由后端统一清理账号、channel、cookie 和 proxy assignment。"
    ),
)
async def delete_channel(channel_id: str = Path(description="渠道 ID，由登录会话签发，用于账号绑定和发文。")):
    require_valid_channel_id(channel_id)
    deleted = agent.delete_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return ChannelDeleteResponse(channel_id=channel_id, deleted=True)
