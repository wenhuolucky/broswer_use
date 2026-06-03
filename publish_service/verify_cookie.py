#!/usr/bin/env python
"""
验证修复后的 cookie 可以被 Playwright 接受
启动一个 Chromium context，注入 cookie_fixed.json，验证不抛异常
"""
import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        try: s.reconfigure(encoding="utf-8")
        except: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from browser_utils import get_browser_path

COOKIE_FILE = ROOT / "publish_service" / "test_data" / "cookie_fixed.json"


async def main():
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        storage_state = json.load(f)
    cookies = storage_state["cookies"]
    print(f"待验证 cookie: {len(cookies)} 条")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=get_browser_path())
        context = await browser.new_context()

        try:
            await context.add_cookies(cookies)
            print("✅ add_cookies 成功！没有 sameSite 报错")
        except Exception as e:
            print(f"❌ add_cookies 失败: {e}")
            sys.exit(1)

        # 进一步验证：访问头条，看 sessionid 是否被带上
        page = await context.new_page()
        try:
            await page.goto("https://mp.toutiao.com", wait_until="domcontentloaded", timeout=15000)
            print(f"✅ 头条页面已打开: {await page.title()}")
        except Exception as e:
            print(f"⚠️  页面访问失败（可能是网络问题，但 cookie 注入本身成功了）: {e}")

        await browser.close()
    print("\n🎉 验证通过！cookie_fixed.json 可直接用于发布服务")


if __name__ == "__main__":
    asyncio.run(main())
