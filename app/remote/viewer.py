"""
远程浏览器查看器 — 通过 CDP screencast 提供实时浏览器画面和操作
优化: 低延迟传输 (binary ws + Blob 渲染 + 帧节流 + 低画质) + 登录自动检测
"""
import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_venv_site = _PROJECT_ROOT / "venv" / "Lib" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

from playwright.async_api import async_playwright
from aiohttp import web

from app.remote.display_config import get_remote_viewer_screencast_options


def _cdp_http_url(cdp_port: int) -> str:
    return f"http://127.0.0.1:{cdp_port}"


# ── 二进制传输 + Blob 渲染 ──
VIEWER_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>远程浏览器查看器</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d1117; color: #c9d1d9; font-family: -apple-system, 'Segoe UI', sans-serif; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
  .toolbar { background: #161b22; border-bottom: 1px solid #30363d; padding: 6px 12px; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .toolbar button { padding: 4px 12px; border: 1px solid #30363d; border-radius: 6px; background: #21262d; color: #c9d1d9; cursor: pointer; font-size: 14px; white-space: nowrap; }
  .toolbar button:hover { background: #30363d; }
  .toolbar input { flex: 1; padding: 4px 10px; border-radius: 6px; border: 1px solid #30363d; background: #0d1117; color: #c9d1d9; font-size: 13px; }
  .viewer { flex: 1; display: flex; justify-content: center; align-items: flex-start; overflow: auto; background: #010409; position: relative; }
  #screen { display: block; image-rendering: auto; }
  #overlay { position: absolute; top: 0; left: 50%; transform: translateX(-50%); cursor: crosshair; }
  #status { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 16px; color: #8b949e; pointer-events: none; }
  .status-ok { color: #3fb950 !important; }
  .status-err { color: #f85149 !important; }
</style>
</head>
<body>
<div class="toolbar">
  <button id="btnBack">&#8592;</button>
  <button id="btnFwd">&#8594;</button>
  <button id="btnReload">&#8635;</button>
  <input id="urlBar" type="text" placeholder="输入网址后按回车...">
</div>
<div class="viewer" id="viewerArea">
  <div id="status">正在连接...</div>
  <div id="overlay" style="display:none">
    <img id="screen">
  </div>
</div>
<script>
const wsProto = location.protocol === 'https:' ? 'wss://' : 'ws://';
const ws = new WebSocket(wsProto + location.host + '/ws');
const img = document.getElementById('screen');
const overlay = document.getElementById('overlay');
const status = document.getElementById('status');
const urlBar = document.getElementById('urlBar');
let devW = 960, devH = 640, blobUrl = null;

ws.binaryType = 'arraybuffer';
ws.onopen = () => { status.textContent = '已连接，等待画面...'; status.className = 'status-ok'; };
ws.onclose = () => { status.textContent = '连接已断开'; status.className = 'status-err'; overlay.style.display = 'none'; };
ws.onerror = () => { status.textContent = 'WebSocket 错误'; status.className = 'status-err'; };

ws.onmessage = (e) => {
  if (e.data instanceof ArrayBuffer) {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    blobUrl = URL.createObjectURL(new Blob([e.data], { type: 'image/jpeg' }));
    img.src = blobUrl;
    overlay.style.display = 'block';
    status.style.display = 'none';
    return;
  }
  try {
    const msg = JSON.parse(e.data);
    if (msg.type === 'url') urlBar.value = msg.url;
    if (msg.type === 'init') { devW = msg.w || 960; devH = msg.h || 640; }
  } catch(_) {}
};

function send(type, data) { if (ws.readyState === 1) ws.send(JSON.stringify({ type, ...data })); }

function toDevice(e) {
  const r = overlay.getBoundingClientRect();
  return {
    x: Math.round((e.clientX - r.left) * devW / r.width),
    y: Math.round((e.clientY - r.top) * devH / r.height),
  };
}

overlay.addEventListener('mousedown', (e) => {
  const p = toDevice(e);
  if (e.button === 0) send('mouseDown', { x: p.x, y: p.y });
  else { e.preventDefault(); send('mouseRight', { x: p.x, y: p.y }); }
});
overlay.addEventListener('mouseup', (e) => { const p = toDevice(e); send('mouseUp', { x: p.x, y: p.y }); });
overlay.addEventListener('contextmenu', e => e.preventDefault());
overlay.addEventListener('wheel', (e) => { const p = toDevice(e); send('wheel', { x: p.x, y: p.y, dx: -e.deltaX, dy: -e.deltaY }); e.preventDefault(); }, { passive: false });

document.addEventListener('keydown', (e) => {
  if (e.target === urlBar) return;
  if (e.key.length === 1 || e.key === 'Enter' || e.key === 'Backspace' || e.key === 'Tab' || e.key === 'Escape' || e.key === 'Delete' || e.key.startsWith('Arrow')) {
    e.preventDefault(); send('key', { text: e.key, code: e.code });
  }
});
urlBar.addEventListener('keydown', (e) => { if (e.key === 'Enter') { send('navigate', { url: urlBar.value }); e.preventDefault(); } });

document.getElementById('btnBack').onclick = () => send('goBack');
document.getElementById('btnFwd').onclick = () => send('goForward');
document.getElementById('btnReload').onclick = () => send('reload');
</script>
</body>
</html>
"""


async def run_viewer_server(
    cdp_port: int, http_port: int = 8888,
    target_url: str = None, login_detect_url: str = None,
    login_event: asyncio.Event = None,
    disconnect_event: asyncio.Event = None,
):
    """启动远程浏览器查看器（低延迟模式）

    Args:
        cdp_port: Chrome CDP 调试端口
        http_port: 查看器 HTTP 服务端口
        target_url: 启动后自动导航到此 URL
        login_detect_url: 登录页 URL 前缀（如 /auth/page/login）
        login_event: 登录检测 Event，URL 离开 login_detect_url 时触发
        disconnect_event: 断开检测 Event，WebSocket 断开时触发
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(_cdp_http_url(cdp_port))
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    if target_url:
        try:
            await page.goto(target_url, timeout=30000, wait_until='domcontentloaded')
        except:
            pass

    cdp = await page.context.new_cdp_session(page)

    # 获取实际设备尺寸
    viewport = await page.evaluate('() => ({ w: window.innerWidth, h: window.innerHeight })')
    dev_w = viewport.get('w', 960)
    dev_h = viewport.get('h', 640)

    app = web.Application()

    async def handle_index(request):
        return web.Response(text=VIEWER_HTML, content_type='text/html')

    async def handle_ws(request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        # 发送初始化消息
        await ws.send_json({'type': 'init', 'w': dev_w, 'h': dev_h})

        screencast_options = get_remote_viewer_screencast_options()
        await cdp.send('Page.startScreencast', screencast_options)

        screencasting = True
        last_frame_time = 0
        FRAME_INTERVAL = 0.1

        async def on_frame(params):
            nonlocal screencasting, last_frame_time
            now = asyncio.get_event_loop().time()
            if now - last_frame_time < FRAME_INTERVAL:
                try:
                    await cdp.send('Page.screencastFrameAck', {'sessionId': params['sessionId']})
                except:
                    pass
                return
            if not screencasting or ws.closed:
                return
            try:
                import base64
                raw = base64.b64decode(params['data'])
                await ws.send_bytes(raw)
                last_frame_time = now
                await cdp.send('Page.screencastFrameAck', {'sessionId': params['sessionId']})
            except:
                screencasting = False

        cdp.on('Page.screencastFrame', on_frame)

        # URL 变化追踪 + 登录检测
        has_login_detected = False
        was_on_login_page = login_detect_url is not None

        async def on_navigated(frame):
            nonlocal has_login_detected, was_on_login_page
            url = frame.url
            if not ws.closed:
                try:
                    await ws.send_json({'type': 'url', 'url': url})
                except:
                    pass

            # 登录检测：从登录页跳转到非登录页
            if login_detect_url and login_event and not has_login_detected:
                if login_detect_url in url:
                    was_on_login_page = True
                elif was_on_login_page:
                    # 用户从登录页跳转到了其他页面 = 登录成功
                    has_login_detected = True
                    login_event.set()

        page.on('framenavigated', on_navigated)

        # 发送初始 URL
        try:
            init_url = page.url
            await ws.send_json({'type': 'url', 'url': init_url})

            # 检测初始 URL 是否已经是非登录页（用户可能已经在后台）
            if login_detect_url and login_event and login_detect_url not in init_url:
                has_login_detected = True
                login_event.set()
        except:
            pass

        # 等待 WebSocket 连接断开
        async for message in ws:
            if message.type == 1:
                try:
                    data = json.loads(message.data)
                    dtype = data['type']

                    if dtype in ('mouseDown', 'mouseUp'):
                        action = 'mousePressed' if dtype == 'mouseDown' else 'mouseReleased'
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': action, 'x': data['x'], 'y': data['y'],
                            'button': data.get('button', 'left'), 'clickCount': 1,
                        })
                    elif dtype == 'mouseRight':
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': 'mousePressed', 'x': data['x'], 'y': data['y'],
                            'button': 'right', 'clickCount': 1,
                        })
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': 'mouseReleased', 'x': data['x'], 'y': data['y'],
                            'button': 'right', 'clickCount': 1,
                        })
                    elif dtype == 'wheel':
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': 'mouseWheel', 'x': data['x'], 'y': data['y'],
                            'deltaX': -data.get('dx', 0), 'deltaY': -data.get('dy', 0),
                        })
                    elif dtype == 'key':
                        text = data['text']
                        if len(text) == 1:
                            await cdp.send('Input.dispatchKeyEvent', {'type': 'rawKeyDown', 'text': text})
                            await cdp.send('Input.dispatchKeyEvent', {'type': 'char', 'text': text})
                            await cdp.send('Input.dispatchKeyEvent', {'type': 'keyUp', 'text': text})
                        elif text in ('Enter', 'Backspace', 'Tab', 'Escape', 'Delete'):
                            for t in ('rawKeyDown', 'keyUp'):
                                await cdp.send('Input.dispatchKeyEvent', {
                                    'type': t, 'key': text, 'code': text,
                                })
                        elif text.startswith('Arrow'):
                            for t in ('rawKeyDown', 'keyUp'):
                                await cdp.send('Input.dispatchKeyEvent', {
                                    'type': t, 'key': text, 'code': text,
                                })
                    elif dtype == 'navigate':
                        await page.goto(data['url'], timeout=30000, wait_until='domcontentloaded')
                    elif dtype == 'goBack':
                        await page.go_back()
                    elif dtype == 'goForward':
                        await page.go_forward()
                    elif dtype == 'reload':
                        await page.reload()
                except Exception as e:
                    print(f'操作错误: {e}')

        # WebSocket 连接已断开（用户关闭页面）
        if disconnect_event:
            disconnect_event.set()

        screencasting = False
        try:
            await cdp.send('Page.stopScreencast')
        except:
            pass
        return ws

    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', handle_ws)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', http_port)
    await site.start()

    options = get_remote_viewer_screencast_options()
    print(
        "[查看器] 已启动: "
        f"http://localhost:{http_port} "
        f"(quality={options['quality']}, {options['maxWidth']}x{options['maxHeight']}, ~10fps)"
    )
    return runner, pw, browser, cdp, page


if __name__ == '__main__':
    print("remote_viewer is used internally by the publish service")
