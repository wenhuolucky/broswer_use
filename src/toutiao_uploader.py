#!/usr/bin/env python3
"""
头条号图片上传模块
通过浏览器自动化上传图片到头条号编辑器，返回图片 URL
"""

import asyncio
from pathlib import Path
from typing import List, Optional

from src.browser_utils import get_browser_path


async def _upload_single_image(
    browser_path: str,
    auth_file: str,
    image_path: str,
    insert_into_editor: bool = True,
) -> Optional[str]:
    """
    上传单张图片到头条号。

    Args:
        browser_path: 浏览器路径（Edge 或 Chrome）
        auth_file: auth.json 路径
        image_path: 本地图片路径
        insert_into_editor: True=插入编辑器正文, False=仅上传获取URL(用于封面)

    Returns:
        上传后的图片 URL，失败返回 None
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=browser_path)
        context = await browser.new_context(
            storage_state=auth_file,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        try:
            await page.goto(
                "https://mp.toutiao.com/profile_v4/graphic/publish",
                wait_until="load",
                timeout=30000,
            )
            await asyncio.sleep(3)

            # 关闭可能存在的 AI 助手遮罩
            try:
                await page.locator(".byte-drawer-mask").click(timeout=5000)
                await asyncio.sleep(1)
            except Exception:
                pass

            # 点击图片按钮（工具栏第 12 个按钮，索引 11）
            await page.locator(".syl-toolbar-button").nth(11).click()
            await asyncio.sleep(2)

            # 通过第一个 file input 上传文件
            await page.locator('input[type="file"]').first.set_input_files(image_path)
            await asyncio.sleep(3)

            # 获取上传后的图片 URL
            uploaded_url = await page.evaluate("""() => {
                const drawer = document.querySelector('.mp-ic-img-drawer');
                if (!drawer) return null;
                const img = drawer.querySelector('img');
                return img ? img.src : null;
            }""")

            if not uploaded_url:
                print(f"  错误: 未能获取上传后的图片 URL")
                return None

            # 点击图片确认按钮插入编辑器（正文图片需要）
            if insert_into_editor:
                confirm_btn = page.locator(
                    ".mp-ic-img-drawer .byte-btn-primary.byte-btn-size-large"
                )
                await confirm_btn.click()
                await asyncio.sleep(2)

            return uploaded_url

        except Exception as e:
            print(f"  错误: {e}")
            return None
        finally:
            await browser.close()


async def upload_images(
    auth_file_path: str,
    image_paths: List[str],
) -> List[str]:
    """
    批量上传图片到头条号，返回图片 URL 列表。

    第一张图片只上传不插入（作为封面），其余图片上传并插入编辑器正文。

    Returns:
        (cover_url, body_image_urls) 元组
    """
    browser_path = get_browser_path()
    if not browser_path:
        print("错误: 找不到浏览器（请在 .env 中设置 BROWSER_EXECUTABLE_PATH 或 BROWSER_TYPE=chrome）")
        return []

    uploaded_urls = []

    for i, image_path in enumerate(image_paths):
        print(f"正在上传图片 [{i+1}/{len(image_paths)}]: {image_path}")
        # 第一张只上传获取 URL（用于封面），不插入编辑器
        insert = i > 0
        url = await _upload_single_image(browser_path, auth_file_path, image_path, insert)
        if url:
            uploaded_urls.append(url)
            print(f"  上传成功")
        else:
            print(f"  上传失败，跳过")

    return uploaded_urls
