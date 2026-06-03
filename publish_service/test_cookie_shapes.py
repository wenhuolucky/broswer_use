#!/usr/bin/env python
"""
回归测试：6 种 JSON 形态的 cookie 端到端验证
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

from publish_service.publish_service import PublishService
from playwright.async_api import async_playwright
from browser_utils import get_browser_path

# 6 种 JSON 形态的输入
SAMPLE_COOKIE = {
    "name": "sessionid",
    "value": "test_abc123",
    "domain": ".toutiao.com",
    "httpOnly": True,
    "secure": False,
    "sameSite": "no_restriction",   # ← Chrome 扩展导出格式（应被规范化为 "None"）
    "expirationDate": 1785056256.28349,  # ← 应被改名为 expires
    "path": "/",
}

VARIANTS = {
    "1.list": json.dumps([SAMPLE_COOKIE], ensure_ascii=False),
    "2.storage_state": json.dumps(
        {"cookies": [SAMPLE_COOKIE], "origins": []}, ensure_ascii=False
    ),
    "3.cookie_单数": json.dumps({"cookie": [SAMPLE_COOKIE]}, ensure_ascii=False),
    "4.嵌套_data": json.dumps(
        {"data": {"cookies": [SAMPLE_COOKIE]}}, ensure_ascii=False
    ),
    "5.字典_kv": json.dumps(
        {"sessionid": "test_abc123", "sid_tt": "test_xyz789"}, ensure_ascii=False
    ),
    "6.results_字段": json.dumps({"results": [SAMPLE_COOKIE]}, ensure_ascii=False),
}


async def test_one(p, name, raw):
    svc = PublishService()
    parsed = svc._parse_cookie_string(raw)
    assert len(parsed) > 0, f"[{name}] 解析结果为空"

    # 验证规范化
    for c in parsed:
        if "sameSite" in c:
            assert c["sameSite"] in ("Strict", "Lax", "None"), \
                f"[{name}] sameSite 未规范: {c['sameSite']!r}"
        assert "expirationDate" not in c, f"[{name}] 未移除 expirationDate 字段"
        assert "storeId" not in c, f"[{name}] 未移除 storeId 字段"

    # Playwright 验证
    browser = await p.chromium.launch(headless=True, executable_path=get_browser_path())
    context = await browser.new_context()
    try:
        await context.add_cookies(parsed)
        return True, len(parsed)
    except Exception as e:
        return False, str(e)
    finally:
        await browser.close()


async def main():
    async with async_playwright() as p:
        all_pass = True
        for name, raw in VARIANTS.items():
            ok, info = await test_one(p, name, raw)
            status = "✅" if ok else "❌"
            print(f"{status} [{name}] -> {info}")
            if not ok:
                all_pass = False
        print()
        if all_pass:
            print("🎉 6 种 JSON 形态全部通过！")
        else:
            print("⚠️  存在失败用例")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
