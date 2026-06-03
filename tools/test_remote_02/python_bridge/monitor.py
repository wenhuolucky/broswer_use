"""
登录监控器 — 监控 Docker 中 Chrome 的 URL 变化，检测到登录后自动提取 cookies
"""
import asyncio
import json
import sys
from pathlib import Path
from collections import Counter

# 确保使用项目 venv
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_venv_site = _PROJECT_ROOT / "venv" / "Lib" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

# 将 test_remote_02 加入搜索路径，保证 import python_bridge.xxx 可用
_MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_ROOT))


async def monitor_and_extract(
    cdp_url: str = "http://localhost:9222",
    login_url_prefix: str = "/auth/page/login",
    poll_interval: float = 1.0,
    timeout: int = 600,
    output_path: str = "output/cookies.json",
):
    """一体化：等待用户登录 → 自动提取 cookies（共用同一个连接）

    Args:
        cdp_url: Chrome CDP 调试地址
        login_url_prefix: 登录页 URL 前缀
        poll_interval: 轮询间隔（秒）
        timeout: 超时时间（秒）
        output_path: Cookie 输出路径

    Returns:
        cookie 列表，失败返回 None
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None

    try:
        # ---- 连接 CDP ----
        print(f"[监控] 正在连接 CDP: {cdp_url} ...")
        browser = await asyncio.wait_for(
            pw.chromium.connect_over_cdp(cdp_url),
            timeout=15
        )
        print(f"[监控] 连接成功，正在等待用户登录（检测 URL 离开 {login_url_prefix}）...")

        # ---- 等待登录 ----
        elapsed = 0
        login_detected = False

        while elapsed < timeout:
            contexts = browser.contexts
            if contexts:
                pages = contexts[0].pages
                for page in pages:
                    try:
                        url = page.url
                        if login_url_prefix not in url and url != "about:blank":
                            print(f"[监控] ✅ 检测到登录成功! URL: {url}")
                            login_detected = True
                            break
                    except Exception:
                        pass
            if login_detected:
                break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            if elapsed % 30 == 0:
                print(f"[监控] 等待中... ({int(elapsed)}s / {timeout}s)")

        if not login_detected:
            print(f"[监控] ❌ 超时 ({timeout}s)，未检测到登录")
            return None

        # 等待页面加载稳定，确保 cookies 已全部写入
        print(f"[提取] 等待 3 秒让页面稳定...")
        await asyncio.sleep(3)

        # ---- 提取 Cookies（复用同一连接） ----
        print(f"[提取] 正在提取 cookies...")
        contexts = browser.contexts
        if not contexts:
            print("[提取] ❌ 没有活跃的 browser context")
            return None

        ctx = contexts[0]
        cookies = await ctx.cookies()

        if not cookies:
            print("[提取] ❌ 未获取到任何 cookie")
            return None

        # 保存为 Playwright storage_state 格式
        storage_state = {
            "cookies": [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c["path"],
                    "expires": c.get("expires", -1),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                    "sameSite": c.get("sameSite", "Lax"),
                }
                for c in cookies
            ],
            "origins": [],
        }

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(storage_state, f, ensure_ascii=False, indent=2)

        print(f"[提取] ✅ 已提取 {len(cookies)} 个 cookies → {output_file.resolve()}")

        # 打印域名分布
        domains = Counter(c.get("domain", "unknown") for c in cookies)
        print("[提取] Cookie 域名分布:")
        for d, n in domains.most_common(10):
            print(f"  {d}: {n}")

        return cookies

    except asyncio.TimeoutError:
        print(f"[监控] ❌ 无法连接 CDP ({cdp_url})，请确认 Docker 容器已启动")
        return None
    except Exception as e:
        print(f"[监控] ❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="监控 Chrome 登录状态，自动提取 cookies")
    parser.add_argument("--cdp-url", default="http://localhost:9222", help="Chrome CDP 地址")
    parser.add_argument("--login-prefix", default="/auth/page/login", help="登录页 URL 前缀")
    parser.add_argument("--timeout", type=int, default=600, help="超时时间（秒，默认 600）")
    parser.add_argument("--output", default="output/cookies.json", help="Cookie 输出路径")
    args = parser.parse_args()

    cookies = await monitor_and_extract(
        cdp_url=args.cdp_url,
        login_url_prefix=args.login_prefix,
        timeout=args.timeout,
        output_path=args.output,
    )

    if cookies:
        print(f"\n🎉 完成! 共提取 {len(cookies)} 个 cookies")
    else:
        print("\n❌ 提取失败!")


if __name__ == "__main__":
    asyncio.run(main())
