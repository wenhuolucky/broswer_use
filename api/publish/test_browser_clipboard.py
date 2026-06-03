"""Manual browser clipboard verification helper for Toutiao rich-text paste."""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api.publish.markdown_to_rich import WRITE_HTML_TO_BROWSER_CLIPBOARD_JS, markdown_to_html

TEST_MD = """# Browser Clipboard Test

This is **bold** and *italic* mixed text.

[Example Link](https://example.com)

![Network Image](https://q5.itc.cn/images01/20260417/af1e15cf52c54c5282e8415acafec61a.png)
"""

TARGET_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"


async def main():
    from playwright.async_api import async_playwright
    from browser_utils import get_browser_path

    html_content = markdown_to_html(TEST_MD)
    print(f"[1] HTML length: {len(html_content)}")
    print(f"[2] HTML preview:\n{html_content[:200]}...\n")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            executable_path=get_browser_path(),
            args=["--remote-debugging-port=9222"],
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        print(f"[3] Open {TARGET_URL}")
        print("    Log in manually and leave the publish editor page open.")
        for remaining in range(60, 0, -1):
            print(f"    Waiting {remaining:>2d}s...", end="\r")
            await asyncio.sleep(1)
        print("\n[4] Writing HTML to browser clipboard...")

        result = await page.evaluate(WRITE_HTML_TO_BROWSER_CLIPBOARD_JS, html_content)
        print(f"    Clipboard result: {result}")

        focus_result = await page.evaluate(
            """
            () => {
                const editor = document.querySelector('.ProseMirror')
                    || document.querySelector('[contenteditable="true"]')
                    || document.querySelector('div[contenteditable]');
                if (!editor) {
                    return { ok: false, error: 'editor not found' };
                }
                editor.focus();
                return { ok: true, tag: editor.tagName, className: editor.className };
            }
            """
        )
        print(f"[5] Focus result: {focus_result}")

        await page.keyboard.press("Control+v")
        await page.wait_for_timeout(3000)

        editor_state = await page.evaluate(
            """
            () => {
                const editor = document.querySelector('.ProseMirror')
                    || document.querySelector('[contenteditable="true"]')
                    || document.querySelector('div[contenteditable]');
                return {
                    text: editor ? editor.innerText.substring(0, 300) : '(missing)',
                    htmlLen: editor ? editor.innerHTML.length : 0,
                    hasStrong: editor ? !!editor.querySelector('strong') : false,
                    hasLink: editor ? !!editor.querySelector('a[href]') : false,
                    hasImage: editor ? !!editor.querySelector('img') : false,
                };
            }
            """
        )
        print(f"[6] Editor state:\n{editor_state}\n")
        print("Check whether network images and links were pasted from HTML.")
        await page.wait_for_timeout(30000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
