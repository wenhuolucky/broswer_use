from __future__ import annotations

import asyncio
import contextlib

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

router = APIRouter(tags=["vnc"])

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

# 登录 viewer 不需要 KasmVNC 左侧控制栏(Keys/Clipboard/Settings/Disconnect…)。
# 整个面板及其边缘展开把手都挂在 #noVNC_control_bar_anchor 下。
#
# 注意：不能用 display:none 把它整棵子树端掉——noVNC 真正捕获键盘输入的隐藏
# <textarea id="noVNC_keyboardinput"> 就嵌在这棵子树里，display:none 的元素无法
# 获得焦点、收不到键盘事件，会导致 live url 鼠标能点但敲键盘没反应。
# 改为「视觉隐藏但保留在渲染树」：opacity:0 让面板不可见、keyboardinput 仍可聚焦；
# pointer-events:none 让鼠标点击穿透到远程画面、同时屏蔽控制栏的悬停展开。
# 可用 REMOTE_VIEWER_HIDE_CONTROL_BAR=0 关闭注入。
_HIDE_CONTROL_BAR_CSS = (
    "<style>#noVNC_control_bar_anchor{"
    "opacity:0!important;pointer-events:none!important;}</style>"
)


def _hide_control_bar_enabled() -> bool:
    import os

    return os.getenv("REMOTE_VIEWER_HIDE_CONTROL_BAR", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _inject_hide_control_bar(html: str) -> str:
    lowered = html.lower()
    for anchor in ("</head>", "</body>"):
        idx = lowered.rfind(anchor)
        if idx != -1:
            return html[:idx] + _HIDE_CONTROL_BAR_CSS + html[idx:]
    return html + _HIDE_CONTROL_BAR_CSS


def _runner(request_or_websocket):
    runner = getattr(request_or_websocket.app.state, "remote_login_runner", None)
    if runner is not None:
        return runner
    try:
        from app.api.routes import agent

        return agent.remote_runner
    except Exception:
        return None


def _session_port(runner, session_id: str) -> int | None:
    # 鉴权完全依赖 session_id 不可猜（uuid4 的 32 位十六进制 = 128 bit 随机），
    # 它本来就在 URL 路径里，等价于 bearer 凭证；会话存在即放行，无需额外 token。
    if runner is None:
        return None
    session = runner.get(session_id)
    if session is None:
        return None
    port = int(getattr(session, "viewer_port", 0) or 0)
    return port or None


@router.api_route(
    "/vnc/{session_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def vnc_http(session_id: str, path: str, request: Request) -> Response:
    port = _session_port(_runner(request), session_id)
    if port is None:
        return Response(status_code=404, content="session not found")

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
    upstream_resp = await client.send(upstream_req, stream=True)
    headers = {
        key: value
        for key, value in upstream_resp.headers.items()
        if key.lower() not in _RESP_DROP
    }
    content_type = upstream_resp.headers.get("content-type", "")
    if "text/html" in content_type.lower() and _hide_control_bar_enabled():
        # HTML 页面较小：缓冲后注入隐藏控制栏的 CSS，再整体返回。
        # 用 aread() 取已解码(去除 content-encoding)的正文，content-length 由 Response 重算。
        raw = await upstream_resp.aread()
        status_code = upstream_resp.status_code
        await _aclose(upstream_resp, client)
        body = _inject_hide_control_bar(
            raw.decode("utf-8", errors="replace")
        ).encode("utf-8")
        response = Response(content=body, status_code=status_code, headers=headers)
    else:
        response = StreamingResponse(
            upstream_resp.aiter_raw(),
            status_code=upstream_resp.status_code,
            headers=headers,
            background=BackgroundTask(_aclose, upstream_resp, client),
        )
    return response


async def _aclose(resp: httpx.Response, client: httpx.AsyncClient) -> None:
    await resp.aclose()
    await client.aclose()


@router.websocket("/vnc/{session_id}/{path:path}")
async def vnc_ws(websocket: WebSocket, session_id: str, path: str) -> None:
    port = _session_port(_runner(websocket), session_id)
    if port is None:
        await websocket.close(code=4404)
        return

    requested = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    qs = websocket.url.query
    upstream_url = f"ws://127.0.0.1:{port}/{path}" + (f"?{qs}" if qs else "")
    try:
        async with websockets.connect(
            upstream_url,
            max_size=None,
            open_timeout=10,
            # 纯二进制透传代理：关掉 websockets 自带的保活 ping。
            # VNC 协议自身有活动；KasmVNC 上游在持续编码(如登录页背景视频)、
            # CPU 吃满时不一定及时回 WS 层 pong，默认 20s ping_timeout 会误判
            # 超时并以 1011 断开，引发反复重连/画面抖动。
            ping_interval=None,
            ping_timeout=None,
            subprotocols=requested or None,
            additional_headers={"Origin": f"http://127.0.0.1:{port}"},
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)
            await _bridge(websocket, upstream)
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


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
            # 上游断开是正常收尾（用户关闭/会话结束），不当异常上抛。
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
    # 取走已完成任务的异常，避免 "Task exception was never retrieved" 噪音日志。
    for task in done:
        with contextlib.suppress(Exception):
            task.result()
