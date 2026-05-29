#!/usr/bin/env python3
"""
远程浏览器 Cookie 采集工具 — 本地 Chrome + Cloudflare Tunnel + 自定义查看器

原理:
  1. 启动本地 Chrome，开启 CDP 调试端口 (9222)
  2. 启动查看器 (aiohttp)，通过 CDP screencast 实时渲染浏览器画面
  3. Cloudflare Tunnel 暴露查看器到公网
  4. 远程用户通过网页查看画面、点击、输入
  5. 用户完成登录后，通过 CDP 提取 cookies

用法:
  venv/Scripts/python.exe -m browser_test.remote_login --platform toutiao
  venv/Scripts/python.exe -m browser_test.remote_login --platform toutiao --verify <auth_file>
"""
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

# 确保使用项目 venv
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_venv_site = _PROJECT_ROOT / "venv" / "Lib" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

# Windows UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from playwright.async_api import async_playwright

from browser_test.config import get_platform, get_default_auth_file
from browser_test.cookie_store import CookieStore

CDP_PORT = 9222
VIEWER_PORT = 8888


def find_chrome_path():
    """查找 Chrome 可执行文件路径"""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def find_cloudflared_path():
    """查找 cloudflared 可执行文件路径"""
    candidates = [
        r"C:\Program Files\Cloudflare\cloudflared.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\cloudflared\cloudflared.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\local\bin\cloudflared.exe"),
    ]

    # WinGet 安装路径
    winget_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.exists(winget_dir):
        for d in os.listdir(winget_dir):
            if "cloudflare" in d.lower():
                cf_path = os.path.join(winget_dir, d, "cloudflared.exe")
                if os.path.exists(cf_path):
                    return cf_path

    for path in candidates:
        if os.path.exists(path):
            return path

    found = shutil.which("cloudflared")
    if found:
        return found

    return None


async def launch_chrome(cdp_port: int) -> subprocess.Popen:
    """启动 Chrome 并开启 CDP 调试端口"""
    chrome_path = find_chrome_path()
    if not chrome_path:
        print("错误: 未找到 Chrome 或 Edge 浏览器")
        sys.exit(1)

    print(f"[Chrome] 使用浏览器: {chrome_path}")

    user_data_dir = _PROJECT_ROOT / "chrome_profile_remote"

    # 清理上一次会话的 cookie/缓存，确保每次都是干净的浏览器
    if user_data_dir.exists():
        import shutil
        shutil.rmtree(user_data_dir, ignore_errors=True)
        print(f"[Chrome] 已清理旧会话: {user_data_dir}")
    user_data_dir.mkdir(parents=True, exist_ok=True)

    args = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-default-apps",
        "--new-window",
        "about:blank",
    ]

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(2)
    print(f"[Chrome] 已启动 (CDP 端口: {cdp_port})")
    return proc


async def start_cloudflared_tunnel(local_port: int):
    """启动 Cloudflare Tunnel

    Returns:
        (process, tunnel_url) 元组
    """
    cloudflared_path = find_cloudflared_path()
    if not cloudflared_path:
        print("错误: 未找到 cloudflared")
        print("请安装: winget install Cloudflare.cloudflared")
        sys.exit(1)

    print("[Tunnel] 正在启动 Cloudflare Tunnel...")

    proc = subprocess.Popen(
        [cloudflared_path, "tunnel", "--url", f"http://localhost:{local_port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    tunnel_url = None
    timeout = 30

    while tunnel_url is None:
        if proc.poll() is not None:
            raise RuntimeError(f"cloudflared 异常退出 (code={proc.poll()})")

        line = proc.stdout.readline()
        if not line:
            await asyncio.sleep(0.1)
            continue

        line = line.strip()
        # 显示 tunnel URL
        if "trycloudflare.com" in line:
            match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
            if match:
                tunnel_url = match.group(0)
                break
        elif "Tunnel" in line or "quick" in line.lower() or "created" in line.lower():
            print(f"  [Tunnel] {line}")

        await asyncio.sleep(0.1)

    print(f"[Tunnel] 已创建: {tunnel_url}")
    return proc, tunnel_url


async def navigate_to_login(cdp_port: int, target_url: str):
    """通过 CDP 导航到指定 URL"""
    pw = await async_playwright().start()
    try:
        browser = await asyncio.wait_for(
            pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}"),
            timeout=15
        )
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
        print(f"[页面] 已导航到: {await page.title()}")
        await browser.close()
    except Exception as e:
        print(f"[页面] 导航失败: {e}")
    finally:
        await pw.stop()


async def extract_cookies(cdp_port: int) -> list:
    """通过 CDP 提取所有 cookies"""
    pw = await async_playwright().start()
    try:
        browser = await asyncio.wait_for(
            pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}"),
            timeout=15
        )
        ctx = browser.contexts[0]
        cookies = await ctx.cookies()
        await browser.close()
        return cookies
    except Exception as e:
        print(f"[Cookie] 提取失败: {e}")
        return []
    finally:
        await pw.stop()


async def cmd_save(platform_name: str, cdp_port: int = CDP_PORT,
                   viewer_port: int = VIEWER_PORT, output_path: Path = None):
    """执行远程登录采集流程"""
    platform = get_platform(platform_name)
    if output_path is None:
        output_path = get_default_auth_file(platform_name)

    chrome_proc = None
    tunnel_proc = None
    viewer_runner = None

    try:
        # 1. 启动 Chrome
        chrome_proc = await launch_chrome(cdp_port)

        # 2. 启动查看器 + 导航
        from browser_test.viewer import run_viewer_server
        print(f"\n[2/5] 正在启动查看器并导航到 {platform.name}...")
        viewer_runner, pw, browser, cdp = await run_viewer_server(
            cdp_port, viewer_port, platform.home_url
        )

        # 3. 启动 Cloudflare Tunnel
        print(f"\n[3/5] 正在创建 Tunnel...")
        tunnel_proc, tunnel_url = await start_cloudflared_tunnel(viewer_port)

        print(f"\n{'='*60}")
        print(f"[4/5] 远程用户访问链接:")
        print(f"")
        print(f"  >>> {tunnel_url} <<<")
        print(f"")
        print(f"{'='*60}")
        print(f"\n将以上链接发送给远程用户。")
        print(f"用户在网页中可以看到实时浏览器画面并进行操作。")
        print()

        # 4. 等待用户完成登录
        print("[5/5] 等待用户完成登录...")
        print("         （按 Enter 继续，或输入 quit 取消）")
        try:
            loop = asyncio.get_event_loop()
            user_input = await loop.run_in_executor(None, lambda: input(">>> "))
            if user_input.strip().lower() in ("quit", "q", "exit", "cancel"):
                print("操作已取消。")
                return
        except (EOFError, KeyboardInterrupt):
            print("\n用户中断。")
            return

        # 提取 cookies
        print("\n正在提取 cookies...")
        cookies = await extract_cookies(cdp_port)

        if not cookies:
            print("警告: 未提取到任何 cookies")
            return

        # 保存 cookies
        CookieStore.save(cookies, output_path)
        print(f"已保存 {len(cookies)} 个 cookies 到: {output_path}")

        # 验证
        if CookieStore.validate(cookies, platform):
            print(f"验证通过: 包含 {platform.name} 域名 cookie")
        else:
            print(f"警告: 未检测到 {platform.name} 的域名 cookie，登录可能未成功")

    finally:
        # 清理
        if viewer_runner:
            print("[清理] 关闭查看器...")
            try:
                await viewer_runner.cleanup()
            except:
                pass
            try:
                await browser.close()
                await pw.stop()
            except:
                pass

        if tunnel_proc:
            print("[清理] 关闭 Cloudflare Tunnel...")
            if sys.platform == "win32":
                tunnel_proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                tunnel_proc.terminate()
            try:
                tunnel_proc.wait(timeout=5)
            except:
                tunnel_proc.kill()

        if chrome_proc:
            print("[清理] 关闭 Chrome...")
            chrome_proc.kill()

    print("\n完成！")


async def cmd_verify(platform_name: str, auth_file: Path, cdp_port: int = CDP_PORT):
    """验证已保存的 cookies"""
    platform = get_platform(platform_name)

    if not auth_file.exists():
        print(f"错误: 找不到文件 {auth_file}")
        sys.exit(1)

    auth_data = CookieStore.load(auth_file)
    cookies = auth_data.get("cookies", [])

    if not cookies:
        print(f"错误: {auth_file} 中没有 cookies")
        sys.exit(1)

    print(f"[验证] 已从 {auth_file} 加载 {len(cookies)} 个 cookies")

    if CookieStore.validate(cookies, platform):
        print(f"[验证] Cookie 域名验证通过")
    else:
        print(f"警告: 未检测到 {platform.name} 的域名 cookie")

    # 启动 Chrome + 查看器 + Tunnel
    chrome_proc = await launch_chrome(cdp_port)
    from browser_test.viewer import run_viewer_server
    viewer_runner, pw, browser, cdp = await run_viewer_server(cdp_port, VIEWER_PORT, platform.home_url)

    # 注入 cookies
    ctx = browser.contexts[0]
    print(f"[验证] 正在注入 {len(cookies)} 个 cookies...")
    await ctx.add_cookies(cookies)

    # 重新导航
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(platform.home_url, timeout=30000, wait_until="domcontentloaded")
    print(f"[验证] 已导航到: {await page.title()}")

    tunnel_proc, tunnel_url = await start_cloudflared_tunnel(VIEWER_PORT)

    print(f"\n[验证] 请在远程浏览器中检查登录状态:")
    print(f"  {tunnel_url}")
    print(f"  按 Enter 关闭...")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: input(">>> "))
    except (EOFError, KeyboardInterrupt):
        pass

    # 清理
    try:
        await viewer_runner.cleanup()
    except:
        pass
    try:
        await browser.close()
        await pw.stop()
    except:
        pass
    if tunnel_proc:
        tunnel_proc.kill()
    if chrome_proc:
        chrome_proc.kill()

    print("[验证] 完成")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="远程浏览器 Cookie 采集工具 — 本地 Chrome + Cloudflare Tunnel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  venv/Scripts/python.exe -m browser_test.remote_login --platform toutiao
  venv/Scripts/python.exe -m browser_test.remote_login --platform toutiao --verify auth.json
        """,
    )
    parser.add_argument("--platform", type=str, default="toutiao", help="平台名称")
    parser.add_argument("--cdp-port", type=int, default=CDP_PORT, help="CDP 端口 (默认 9222)")
    parser.add_argument("--viewer-port", type=int, default=VIEWER_PORT, help="查看器端口 (默认 8888)")
    parser.add_argument("--verify", type=str, metavar="AUTH_FILE", help="验证已保存的 Cookie")
    parser.add_argument("--output", type=str, metavar="OUTPUT_FILE", help="Cookie 保存路径")

    args = parser.parse_args()

    if args.verify:
        asyncio.run(cmd_verify(args.platform, Path(args.verify), args.cdp_port))
    else:
        output_path = Path(args.output) if args.output else None
        asyncio.run(cmd_save(args.platform, args.cdp_port, args.viewer_port, output_path))


if __name__ == "__main__":
    main()
