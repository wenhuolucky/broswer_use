"""
Cookie 提取工具 — 通过 CDP 连接 Docker 中的 Chrome，提取 cookies
"""
import asyncio
import json
from pathlib import Path


async def extract_cookies(cdp_url: str = "http://localhost:9222", output_path: str = "output/cookies.json") -> list:
    """通过 CDP 提取 Chrome 的所有 cookies

    Args:
        cdp_url: Chrome CDP 调试地址
        output_path: 保存路径

    Returns:
        cookie 列表
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await asyncio.wait_for(
            pw.chromium.connect_over_cdp(cdp_url),
            timeout=15
        )
        contexts = browser.contexts
        if not contexts:
            print("警告: 没有活跃的 browser context")
            await browser.close()
            await pw.stop()
            return []

        ctx = contexts[0]
        cookies = await ctx.cookies()

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

        print(f"已提取 {len(cookies)} 个 cookies 到: {output_file}")

        # 打印域名分布
        from collections import Counter
        domains = Counter(c.get("domain", "unknown") for c in cookies)
        print("Cookie 域名分布:")
        for d, n in domains.most_common(10):
            print(f"  {d}: {n}")

        await browser.close()
        return cookies
    except Exception as e:
        print(f"Cookie 提取失败: {e}")
        return []
    finally:
        await pw.stop()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="提取 Docker 中 Chrome 的 cookies")
    parser.add_argument("--cdp-url", default="http://localhost:9222", help="Chrome CDP 地址")
    parser.add_argument("--output", default="output/cookies.json", help="输出路径")
    args = parser.parse_args()

    cookies = await extract_cookies(args.cdp_url, args.output)
    if cookies:
        print(f"\n成功! 共 {len(cookies)} 个 cookies")
    else:
        print("\n失败! 未提取到 cookies")


if __name__ == "__main__":
    asyncio.run(main())
