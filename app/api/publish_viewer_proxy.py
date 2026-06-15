"""发文实时查看（publish-viewer）反向代理。

发文浏览器是无头 CDP 浏览器，由 app/remote/viewer.py 起一个本机 aiohttp 截屏服务
（绑定 127.0.0.1:{随机端口}）。该端口在容器里并未对外映射，直连地址只有服务器本机能开。

为了让它和登录 live_url 一样「哪都能打开」，这里仿照 app/api/vnc_proxy.py：在主服务
端口（8833）上挂一个 /publish-viewer/{job_id}/ 反代（HTTP + WebSocket），转发到本机的
viewer 端口。鉴权完全依赖不可猜的 job_id（uuid4 的 32 位十六进制 = 128 bit 随机），它
本身即凭证——与 vnc_proxy 用 session_id 同一套信任模型，故同样不挂 Bearer。

job_id→viewer_port 的映射由发文后台任务在 viewer 起好后注册（见 orchestrator
on_live_url_ready），任务结束时摘除（_on_background_publish_done）。注册表只在事件循环
内读写，单进程单线程，无需加锁。
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

router = APIRouter(tags=["publish-viewer"])

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}
_RESP_DROP = _HOP_BY_HOP | {"server", "date"}

# job_id -> 本机 viewer 的 aiohttp 端口
_VIEWER_PORTS: dict[str, int] = {}


def register_viewer(job_id: str, viewer_port: int) -> None:
    """发文 viewer 起好后登记其本机端口，使 /publish-viewer/{job_id}/ 可被代理。"""
    if job_id and viewer_port:
        _VIEWER_PORTS[job_id] = int(viewer_port)


def unregister_viewer(job_id: str) -> None:
    """发文任务结束（viewer 已拆）后摘除映射，之后访问该 job 的 viewer 返回 404。"""
    _VIEWER_PORTS.pop(job_id, None)


def _viewer_port(job_id: str) -> int | None:
    return _VIEWER_PORTS.get(job_id) or None


@router.api_route(
    "/publish-viewer/{job_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def publish_viewer_http(job_id: str, path: str, request: Request) -> Response:
    port = _viewer_port(job_id)
    if port is None:
        return Response(status_code=404, content="viewer not found")

    upstream = f"http://127.0.0.1:{port}/{path}"
    fwd_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP | {"host"}
    }
    client = httpx.AsyncClient(timeout=None)
    upstream_req = client.build_request(
        request.method,
        upstream,
        headers=fwd_headers,
        params=dict(request.query_params),
        content=await request.body(),
    )
    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError:
        # viewer 已随任务结束被拆、但映射尚未摘除的窗口，或上游瞬时不可达：
        # 给一个干净的 410「会话已结束」而非裸 500。
        await client.aclose()
        return Response(status_code=410, content="viewer ended")
    headers = {
        key: value
        for key, value in upstream_resp.headers.items()
        if key.lower() not in _RESP_DROP
    }
    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=headers,
        background=BackgroundTask(_aclose, upstream_resp, client),
    )


async def _aclose(resp: httpx.Response, client: httpx.AsyncClient) -> None:
    await resp.aclose()
    await client.aclose()


@router.websocket("/publish-viewer/{job_id}/{path:path}")
async def publish_viewer_ws(websocket: WebSocket, job_id: str, path: str) -> None:
    port = _viewer_port(job_id)
    if port is None:
        await websocket.close(code=4404)
        return

    qs = websocket.url.query
    upstream_url = f"ws://127.0.0.1:{port}/{path}" + (f"?{qs}" if qs else "")
    try:
        async with websockets.connect(
            upstream_url,
            max_size=None,
            open_timeout=10,
            # 纯透传代理：viewer 截屏帧自带活动，关掉 websockets 自带保活 ping，
            # 避免 CPU 吃满时误判超时断开。
            ping_interval=None,
            ping_timeout=None,
            additional_headers={"Origin": f"http://127.0.0.1:{port}"},
        ) as upstream:
            await websocket.accept()
            try:
                await _bridge(websocket, upstream)
            except Exception:
                pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close()


async def _bridge(client_ws: WebSocket, upstream) -> None:
    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await client_ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await upstream.send(msg["text"])
        except WebSocketDisconnect:
            pass

    async def upstream_to_client() -> None:
        try:
            async for message in upstream:
                if isinstance(message, bytes):
                    await client_ws.send_bytes(message)
                else:
                    await client_ws.send_text(message)
        except websockets.exceptions.ConnectionClosed:
            pass

    done, pending = await asyncio.wait(
        {
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        },
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        with contextlib.suppress(Exception):
            task.result()
