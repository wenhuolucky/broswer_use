"""
远程浏览器查看器 — 通过 CDP screencast 提供实时浏览器画面和操作
使用 CDP Input.dispatchMouseEvent 直接发送用户输入，低延迟
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

import base64
from PIL import Image
import io


# ── HTML 查看器 ──
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
  #status { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 16px; color: #8b949e; pointer-events: none; text-align: center; }
  .status-ok { color: #3fb950 !important; }
  .status-err { color: #f85149 !important; }
</style>
</head>
<body>
<div class="toolbar">
  <button id="btnBack" title="后退">&#8592;</button>
  <button id="btnFwd" title="前进">&#8594;</button>
  <button id="btnReload" title="刷新">&#8635;</button>
  <input id="urlBar" type="text" placeholder="输入网址后按回车...">
</div>
<div class="viewer" id="viewerArea">
  <div id="status">正在连接...</div>
  <div id="overlay" style="display:none">
    <img id="screen" style="display:block">
  </div>
</div>
<script>
const wsProto = location.protocol === 'https:' ? 'wss://' : 'ws://';
const ws = new WebSocket(wsProto + location.host + '/ws');
const img = document.getElementById('screen');
const overlay = document.getElementById('overlay');
const status = document.getElementById('status');
const urlBar = document.getElementById('urlBar');

// 设备 viewport 尺寸（Chrome 实际的 CSS 像素）
let devW = 1280, devH = 900;

ws.onopen = () => { status.textContent = '已连接，等待画面...'; status.className = 'status-ok'; };
ws.onclose = () => { status.textContent = '连接已断开'; status.className = 'status-err'; overlay.style.display = 'none'; };
ws.onerror = () => { status.textContent = 'WebSocket 连接失败'; status.className = 'status-err'; };

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === 'frame') {
    img.src = 'data:image/jpeg;base64,' + msg.data;
    if (msg.dw) devW = msg.dw;
    if (msg.dh) devH = msg.dh;
    // 等图片加载后再设置 overlay 尺寸
    img.onload = () => {
      overlay.style.width = img.naturalWidth + 'px';
      overlay.style.height = img.naturalHeight + 'px';
    };
    overlay.style.display = 'block';
    status.style.display = 'none';
    if (msg.url) urlBar.value = msg.url;
  }
  if (msg.type === 'url') urlBar.value = msg.url;
};

function send(type, data) { if (ws.readyState === 1) ws.send(JSON.stringify({ type, ...data })); }

// 坐标转换: 屏幕点击 → 图片像素 → 设备 CSS 像素
function toDevice(e) {
  const r = overlay.getBoundingClientRect();
  const sx = e.clientX - r.left;
  const sy = e.clientY - r.top;
  // 映射到图片实际像素
  const imgX = sx * img.naturalWidth / r.width;
  const imgY = sy * img.naturalHeight / r.height;
  // 映射到设备 CSS 像素（screencast 图片已经是设备 CSS 像素的 1:1 映射，因为 deviceScaleFactor=1）
  return {
    x: Math.round(imgX * devW / img.naturalWidth),
    y: Math.round(imgY * devH / img.naturalHeight),
  };
}

overlay.addEventListener('mousedown', (e) => {
  const p = toDevice(e);
  send('mouseDown', { x: p.x, y: p.y, button: e.button === 2 ? 'right' : 'left' });
});
overlay.addEventListener('mouseup', (e) => {
  const p = toDevice(e);
  send('mouseUp', { x: p.x, y: p.y, button: e.button === 2 ? 'right' : 'left' });
});
overlay.addEventListener('contextmenu', e => e.preventDefault());
overlay.addEventListener('wheel', (e) => {
  const p = toDevice(e);
  send('wheel', { x: p.x, y: p.y, dx: -e.deltaX, dy: -e.deltaY });
  e.preventDefault();
}, { passive: false });

// 键盘事件
document.addEventListener('keydown', (e) => {
  if (e.target === urlBar) return;
  e.preventDefault();
  send('key', { text: e.key });
});
urlBar.addEventListener('keydown', (e) => { if (e.key === 'Enter') { send('navigate', { url: urlBar.value }); e.preventDefault(); } });

document.getElementById('btnBack').onclick = () => send('goBack');
document.getElementById('btnFwd').onclick = () => send('goForward');
document.getElementById('btnReload').onclick = () => send('reload');
</script>
</body>
</html>
"""


async def run_viewer_server(cdp_port: int, http_port: int = 8888, target_url: str = None):
    """启动远程浏览器查看器

    Args:
        cdp_port: Chrome CDP 调试端口
        http_port: 查看器 HTTP 服务端口
        target_url: 启动后自动导航到此 URL
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    if target_url:
        try:
            await page.goto(target_url, timeout=30000, wait_until='domcontentloaded')
        except:
            pass

    # CDP session for screencast + input
    cdp = await page.context.new_cdp_session(page)

    app = web.Application()

    async def handle_index(request):
        return web.Response(text=VIEWER_HTML, content_type='text/html')

    async def handle_ws(request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        # Get actual device viewport dimensions
        viewport = await page.evaluate('() => ({ w: window.innerWidth, h: window.innerHeight })')
        dev_w = viewport.get('w', 1280)
        dev_h = viewport.get('h', 900)

        # Start screencast
        await cdp.send('Page.startScreencast', {
            'format': 'jpeg',
            'quality': 75,
            'maxWidth': 1280,
            'maxHeight': 900,
        })

        screencasting = True

        async def on_frame(params):
            nonlocal screencasting
            if not screencasting or ws.closed:
                return
            try:
                await ws.send_json({
                    'type': 'frame',
                    'data': params['data'],
                    'dw': dev_w,
                    'dh': dev_h,
                })
                await cdp.send('Page.screencastFrameAck', {'sessionId': params['sessionId']})
            except:
                screencasting = False

        cdp.on('Page.screencastFrame', on_frame)

        # Track URL
        async def on_navigated(frame):
            if not ws.closed:
                try:
                    await ws.send_json({'type': 'url', 'url': frame.url})
                except:
                    pass
        page.on('framenavigated', on_navigated)

        # Send initial URL
        try:
            await ws.send_json({'type': 'url', 'url': page.url})
        except:
            pass

        # Handle user input
        async for message in ws:
            if message.type == 1:
                try:
                    data = json.loads(message.data)
                    dtype = data['type']

                    if dtype in ('mouseDown', 'mouseUp'):
                        action = 'mousePressed' if dtype == 'mouseDown' else 'mouseReleased'
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': action,
                            'x': data['x'],
                            'y': data['y'],
                            'button': data.get('button', 'left'),
                            'clickCount': 1,
                        })

                    elif dtype == 'mouseRight':
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': 'mousePressed',
                            'x': data['x'],
                            'y': data['y'],
                            'button': 'right',
                            'clickCount': 1,
                        })
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': 'mouseReleased',
                            'x': data['x'],
                            'y': data['y'],
                            'button': 'right',
                            'clickCount': 1,
                        })

                    elif dtype == 'wheel':
                        await cdp.send('Input.dispatchMouseEvent', {
                            'type': 'mouseWheel',
                            'x': data['x'],
                            'y': data['y'],
                            'deltaX': -data.get('dx', 0),
                            'deltaY': -data.get('dy', 0),
                        })

                    elif dtype == 'key':
                        text = data['text']
                        if len(text) == 1:
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'rawKeyDown',
                                'text': text,
                            })
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'char',
                                'text': text,
                            })
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'keyUp',
                                'text': text,
                            })
                        elif text in ('Enter', 'Backspace', 'Tab', 'Escape', 'Delete'):
                            km = {'Enter': 'Enter', 'Backspace': 'Backspace', 'Tab': 'Tab', 'Escape': 'Escape', 'Delete': 'Delete'}
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'rawKeyDown', 'key': km[text], 'code': km[text],
                            })
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'keyUp', 'key': km[text], 'code': km[text],
                            })
                        elif text.startswith('Arrow'):
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'rawKeyDown', 'key': text, 'code': text,
                            })
                            await cdp.send('Input.dispatchKeyEvent', {
                                'type': 'keyUp', 'key': text, 'code': text,
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

    print(f"[查看器] 已启动: http://localhost:{http_port}")
    return runner, pw, browser, cdp


if __name__ == '__main__':
    print("请使用: venv/Scripts/python.exe -m browser_test.remote_login --platform toutiao")
