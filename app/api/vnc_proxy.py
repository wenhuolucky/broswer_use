from __future__ import annotations

import asyncio
import logging
import time

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.core.config import (
    KASMVNC_WS_PING_INTERVAL,
    KASMVNC_WS_PING_TIMEOUT,
    VNC_HIDE_TOOLBAR,
    VNC_PROXY_KEEPALIVE_INTERVAL,
)
from app.core.request_logging import get_vnc_logger

router = APIRouter(tags=["vnc"])

logger = get_vnc_logger()

_COOKIE_PREFIX = "vncauth_"
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

# CSS injected into the noVNC HTML document to hide the KasmVNC control
# bar/sidebar. The control-bar anchor id is stable across noVNC versions
# (see novnc/noVNC#1371, #941). Extra ids are defensive no-ops if absent.
_HIDE_TOOLBAR_CSS = (
    "<style id=\"vnc-hide-toolbar\">"
    "#noVNC_control_bar_anchor,"
    "#noVNC_control_bar,"
    "#noVNC_control_bar_hint,"
    "#noVNC_status{display:none !important;visibility:hidden !important;}"
    "</style>"
)


def _runner(request_or_websocket):
    runner = getattr(request_or_websocket.app.state, "remote_login_runner", None)
    if runner is not None:
        return runner
    try:
        from app.api.routes import agent

        return agent.remote_runner
    except Exception:
        return None


def _session_port_and_token(runner, session_id: str) -> tuple[int, str] | None:
    if runner is None:
        return None
    session = runner.get(session_id)
    if session is None:
        return None
    port = int(getattr(session, "viewer_port", 0) or 0)
    token = str(getattr(session, "vnc_token", "") or "")
    if not port or not token:
        return None
    return port, token


def _authorized(request: Request, session_id: str, token: str) -> bool:
    query_token = request.query_params.get("token")
    if query_token and query_token == token:
        return True
    cookie = request.cookies.get(_COOKIE_PREFIX + session_id)
    return bool(cookie and cookie == token)


def _is_html_document(path: str, headers: httpx.Headers) -> bool:
    content_type = headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return False
    # Only the top-level document pages need toolbar hiding; skip fragments.
    normalized = path.strip("/").lower()
    return normalized in ("", "index.html", "vnc.html", "vnc_lite.html")


def _inject_hide_toolbar_css(body: bytes) -> bytes:
    if not body:
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    if "vnc-hide-toolbar" in text:
        return body
    lowered = text.lower()
    idx = lowered.rfind("</head>")
    if idx == -1:
        idx = lowered.find("<body")
    if idx == -1:
        return body
    injected = text[:idx] + _HIDE_TOOLBAR_CSS + text[idx:]
    return injected.encode("utf-8")


@router.api_route(
    "/vnc/{session_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def vnc_http(session_id: str, path: str, request: Request) -> Response:
    details = _session_port_and_token(_runner(request), session_id)
    if details is None:
        return Response(status_code=404, content="session not found")
    port, token = details
    if not _authorized(request, session_id, token):
        return Response(status_code=401, content="unauthorized vnc access")

    upstream = f"http://127.0.0.1:{port}/{path}"
    fwd_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP | {"host"}
    }

    # HTML documents are buffered so the hide-toolbar CSS can be injected.
    # Everything else streams through untouched to preserve full noVNC
    # functionality and keep memory flat for large assets.
    if VNC_HIDE_TOOLBAR and request.method in ("GET", "HEAD"):
        async with httpx.AsyncClient(timeout=None) as client:
            upstream_resp = await client.request(
                request.method,
                upstream,
                headers=fwd_headers,
                params=dict(request.query_params),
                content=await request.body(),
            )
            if _is_html_document(path, upstream_resp.headers):
                body = _inject_hide_toolbar_css(upstream_resp.content)
                headers = {
                    key: value
                    for key, value in upstream_resp.headers.items()
                    if key.lower() not in _RESP_DROP
                }
                response = Response(
                    content=body,
                    status_code=upstream_resp.status_code,
                    headers=headers,
                    media_type=upstream_resp.headers.get("content-type"),
                )
                _maybe_set_auth_cookie(response, request, session_id, token)
                return response
            # Not HTML: fall through to streaming path below using fresh client.

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
    response = StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=headers,
        background=BackgroundTask(_aclose, upstream_resp, client),
    )
    _maybe_set_auth_cookie(response, request, session_id, token)
    return response


def _maybe_set_auth_cookie(response: Response, request: Request, session_id: str, token: str) -> None:
    if request.query_params.get("token") == token:
        response.set_cookie(
            _COOKIE_PREFIX + session_id,
            token,
            path=f"/vnc/{session_id}/",
            httponly=True,
            samesite="lax",
        )


async def _aclose(resp: httpx.Response, client: httpx.AsyncClient) -> None:
    await resp.aclose()
    await client.aclose()


@router.websocket("/vnc/{session_id}/{path:path}")
async def vnc_ws(websocket: WebSocket, session_id: str, path: str) -> None:
    details = _session_port_and_token(_runner(websocket), session_id)
    if details is None:
        await websocket.close(code=4404)
        return
    port, token = details
    provided = websocket.query_params.get("token") or websocket.cookies.get(_COOKIE_PREFIX + session_id)
    if provided != token:
        await websocket.close(code=4401)
        return

    requested = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    qs = websocket.url.query
    upstream_url = f"ws://127.0.0.1:{port}/{path}" + (f"?{qs}" if qs else "")
    started = time.monotonic()
    # 0 in config means "disable library auto-ping" — websockets wants None.
    upstream_ping_interval = KASMVNC_WS_PING_INTERVAL or None
    upstream_ping_timeout = KASMVNC_WS_PING_TIMEOUT or None
    logger.info(
        "[vnc-ws] open session=%s path=%s upstream_port=%s keepalive=%ss ping=%s/%s",
        session_id,
        path,
        port,
        VNC_PROXY_KEEPALIVE_INTERVAL,
        upstream_ping_interval,
        upstream_ping_timeout,
        extra={"request_id": session_id},
    )
    try:
        async with websockets.connect(
            upstream_url,
            max_size=None,
            open_timeout=10,
            # Upstream (proxy<->KasmVNC) is a local loopback carrying a live
            # screencast stream. Library auto-ping is DISABLED by default
            # (ping_interval=None) because KasmVNC's websockify never answers
            # WebSocket PING frames, which previously made websockets kill the
            # live connection after 75s ("keepalive ping timeout"). TCP + real
            # traffic prove liveness; a real crash surfaces via the read loop.
            ping_interval=upstream_ping_interval,
            ping_timeout=upstream_ping_timeout,
            subprotocols=requested or None,
            additional_headers={"Origin": f"http://127.0.0.1:{port}"},
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)
            logger.info(
                "[vnc-ws] bridged session=%s subprotocol=%s",
                session_id,
                upstream.subprotocol,
                extra={"request_id": session_id},
            )
            await _bridge(websocket, upstream, session_id, started)
    except Exception as exc:
        # open_timeout / handshake / upstream-unreachable failures land here.
        logger.warning(
            "[vnc-ws] connect-failed session=%s err=%s detail=%s elapsed=%.1fs",
            session_id,
            type(exc).__name__,
            str(exc)[:200],
            time.monotonic() - started,
            extra={"request_id": session_id},
        )
        try:
            await websocket.close()
        except Exception:
            pass


def _close_meta(conn) -> str:
    """Best-effort extraction of a websockets connection's close code/reason."""
    code = getattr(conn, "close_code", None)
    reason = getattr(conn, "close_reason", None)
    if code is None and reason is None:
        return "code=- reason=-"
    return f"code={code} reason={str(reason or '')[:80]!r}"


async def _bridge(client_ws: WebSocket, upstream, session_id: str, started: float) -> None:
    # Per-direction byte/frame counters and a record of which hop ended first,
    # so the logs alone can answer "who hung up, after how long, after how
    # much traffic" without attaching a debugger.
    stats = {
        "c2u_frames": 0,
        "c2u_bytes": 0,
        "u2c_frames": 0,
        "u2c_bytes": 0,
        "keepalive_sent": 0,
        "first_done": "",
        "first_exc": "",
    }

    def _mark(name: str, exc: BaseException | None) -> None:
        if not stats["first_done"]:
            stats["first_done"] = name
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                stats["first_exc"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    async def client_to_upstream() -> None:
        # Browser/cloudflared -> proxy -> KasmVNC. Ends when the downstream
        # (browser side, via cloudflared edge) closes — the prime suspect for
        # the 8-9s drops.
        try:
            while True:
                msg = await client_ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    stats["c2u_frames"] += 1
                    stats["c2u_bytes"] += len(msg["bytes"])
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    stats["c2u_frames"] += 1
                    stats["c2u_bytes"] += len(msg["text"])
                    await upstream.send(msg["text"])
        except WebSocketDisconnect:
            pass

    async def upstream_to_client() -> None:
        # KasmVNC -> proxy -> browser. Ends when the upstream (KasmVNC/Xvnc)
        # closes — points at server-side idle timeout or crash.
        async for message in upstream:
            if isinstance(message, bytes):
                stats["u2c_frames"] += 1
                stats["u2c_bytes"] += len(message)
                await client_ws.send_bytes(message)
            else:
                stats["u2c_frames"] += 1
                stats["u2c_bytes"] += len(message)
                await client_ws.send_text(message)

    async def downstream_keepalive() -> None:
        # Layer 2: Starlette has no automatic ping/pong, so when the user is
        # idle the browser<->proxy hop has zero traffic and intermediate
        # proxies (cloudflared edge) may reap it. Send an empty frame on a
        # fixed interval to keep the hop warm. This is per-connection and
        # purely async — no shared state, no locks, no impact on concurrency.
        if VNC_PROXY_KEEPALIVE_INTERVAL <= 0:
            return
        try:
            while True:
                await asyncio.sleep(VNC_PROXY_KEEPALIVE_INTERVAL)
                await client_ws.send_bytes(b"")
                stats["keepalive_sent"] += 1
        except Exception:
            return

    named = {
        "client_to_upstream": asyncio.create_task(client_to_upstream()),
        "upstream_to_client": asyncio.create_task(upstream_to_client()),
        "downstream_keepalive": asyncio.create_task(downstream_keepalive()),
    }
    done, pending = await asyncio.wait(named.values(), return_when=asyncio.FIRST_COMPLETED)

    for name, task in named.items():
        if task in done:
            _mark(name, task.exception())
    for task in pending:
        task.cancel()

    elapsed = time.monotonic() - started
    # The single line an operator greps for: which hop closed first, how long
    # the session lasted, how much data flowed each way, and the upstream's
    # WebSocket close code/reason.
    log = logger.warning if elapsed < 30 else logger.info
    log(
        "[vnc-ws] closed session=%s first_done=%s elapsed=%.1fs "
        "down->up=%sframes/%sB up->down=%sframes/%sB keepalive=%s upstream(%s)%s",
        session_id,
        stats["first_done"] or "-",
        elapsed,
        stats["c2u_frames"],
        stats["c2u_bytes"],
        stats["u2c_frames"],
        stats["u2c_bytes"],
        stats["keepalive_sent"],
        _close_meta(upstream),
        f" exc={stats['first_exc']}" if stats["first_exc"] else "",
        extra={"request_id": session_id},
    )
