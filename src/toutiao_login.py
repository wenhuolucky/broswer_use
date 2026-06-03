#!/usr/bin/env python3
"""
头条号自动登录脚本
功能：手机号自动填写 → 获取验证码 → 滑块验证（MIMO 识别百分比） → 短信验证码 → 登录

用法:
    python toutiao_login.py --phone 13800138000
"""

import argparse
import asyncio
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from playwright.async_api import async_playwright

from src.browser_utils import get_browser_path
from captcha_solver import (
    execute_drag,
    generate_human_trajectory,
    identify_slider_percent,
)

# Windows 控制台 GBK → UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ============================================================
# 配置常量
# ============================================================

AUTH_FILE = Path(__file__).parent / "toutiao_auth.json"
TOUTIAO_LOGIN_URL = "https://mp.toutiao.com/auth/page/login"
USER_DATA_DIR = Path(__file__).parent / "chrome_profile_toutiao"
EDGE_CDP_PORT = 9226

MAX_CAPTCHA_RETRIES = 3

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'plugin' }))
});
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
for (const key of Object.keys(window)) {
    if (key.match(/^cdc_/) || key.match(/^\\$cdc_/)) {
        try { delete window[key]; } catch (e) {}
    }
}
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) { window.chrome.runtime = {}; }
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
}
"""

PHONE_INPUT_SELECTORS = [
    "input[placeholder*='手机号']",
    "input[type='tel']",
]

GET_CODE_BUTTON_SELECTORS = [
    "span.send-input:has-text('获取验证码')",
    "span:has-text('获取验证码')",
]

CAPTCHA_IFRAME_URL_PART = "verifycenter/captcha"
CAPTCHA_IMAGE_CONTAINER = ".verify-image"
CAPTCHA_SLIDER_BTN_SELECTOR = ".captcha-slider-btn"

SMS_INPUT_SELECTORS = [
    "input[placeholder*='验证码']",
    "input[name='code']",
    "input[maxlength=\"6\"]",
]

LOGIN_BUTTON_SELECTORS = [
    "text=登录",
]

# ============================================================
# 浏览器启动
# ============================================================

async def launch_browser(user_data_dir: Path, cdp_port: int):
    print("[1/5] 启动浏览器...")

    playwright = await async_playwright().start()
    browser_path = get_browser_path()
    browser_name = "Edge" if "edge" in browser_path.lower() else "Chrome"
    print(f"  使用 {browser_name}: {browser_path}")

    user_data_dir.mkdir(parents=True, exist_ok=True)

    ctx = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        headless=False,
        executable_path=browser_path,
        args=[
            f"--remote-debugging-port={cdp_port}",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--start-maximized",
            "--no-default-browser-check",
            "--no-first-run",
        ],
        no_viewport=True,
        ignore_default_args=["--enable-automation"],
    )
    await ctx.add_init_script(_STEALTH_INIT_SCRIPT)
    print("  stealth 脚本已注入")

    return playwright, ctx


async def save_screenshot(page, prefix="error"):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(__file__).parent / f"{prefix}_{ts}.png"
    await page.screenshot(path=str(path), full_page=True)
    print(f"  截图已保存: {path}")
    return path


# ============================================================
# 选择器辅助
# ============================================================

async def find_element(page, selectors: List[str], timeout: float = 10000):
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=int(timeout))
            if el:
                print(f"  找到元素: {sel}")
                return el
        except Exception:
            continue
    print(f"  警告: 所有选择器均未匹配到元素")
    return None


async def find_captcha_iframe(page, timeout: float = 15000):
    import time
    deadline = time.time() + timeout / 1000.0
    while time.time() < deadline:
        for frame in page.frames:
            if CAPTCHA_IFRAME_URL_PART in frame.url:
                print(f"  找到验证码 iframe")
                await asyncio.sleep(1.0)
                return frame
        await asyncio.sleep(0.3)
    return None


# ============================================================
# Phase 2: 手机号填写
# ============================================================

async def fill_phone_number(page, phone: str):
    print("[2/5] 填写手机号...")

    phone_input = await find_element(page, PHONE_INPUT_SELECTORS, timeout=8000)
    if not phone_input:
        raise RuntimeError("未找到手机号输入框")

    await phone_input.click()
    await asyncio.sleep(random.uniform(0.3, 0.8))

    for digit in phone:
        await phone_input.press(digit)
        await asyncio.sleep(random.uniform(0.05, 0.15))

    await page.evaluate("""(input) => {
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
        input.dispatchEvent(new Event('focus', { bubbles: true }));
    }""", phone_input)

    await asyncio.sleep(random.uniform(0.5, 1.2))
    print(f"  手机号已填写: {phone[:3]}****{phone[-4:]}")


# ============================================================
# Phase 3: 滑块验证码
# ============================================================

async def click_get_code_button(page):
    print("[3/5] 点击获取验证码...")

    btn = await find_element(page, GET_CODE_BUTTON_SELECTORS, timeout=8000)
    if not btn:
        raise RuntimeError("未找到「获取验证码」按钮")

    await btn.click()
    print("  已点击，等待滑块验证码出现...")

    captcha_frame = await find_captcha_iframe(page, timeout=15000)
    if not captcha_frame:
        raise RuntimeError("验证码 iframe 未出现")

    print("  滑块验证码已加载")
    return captcha_frame


async def solve_captcha_in_frame(page, captcha_frame):
    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        print(f"\n  [滑块] 第 {attempt}/{MAX_CAPTCHA_RETRIES} 次尝试")

        try:
            # 1. 截图验证码图片区域
            img_container = await captcha_frame.query_selector(CAPTCHA_IMAGE_CONTAINER)
            if not img_container:
                captcha_frame = await find_captcha_iframe(page, timeout=10000)
                if not captcha_frame:
                    raise RuntimeError("验证码 iframe 消失")
                img_container = await captcha_frame.query_selector(CAPTCHA_IMAGE_CONTAINER)
                if not img_container:
                    raise RuntimeError("验证码图片容器不存在")

            screenshot_bytes = await img_container.screenshot()
            print(f"    截图大小: {len(screenshot_bytes)} bytes")

            # 2. MIMO 识别滑块百分比
            print("    MIMO 识别滑块百分比...")
            slider_percent = await identify_slider_percent(screenshot_bytes)
            print(f"    MIMO 识别结果: 滑块应拖到 {slider_percent:.0f}%")

            # 3. 获取滑块轨道和按钮位置
            slider_track = await captcha_frame.query_selector(".captcha-slider-box")
            if not slider_track:
                raise RuntimeError("未找到滑块轨道")

            track_box = await slider_track.bounding_box()
            if not track_box:
                raise RuntimeError("无法获取滑块轨道位置")

            slider_btn = await captcha_frame.query_selector(CAPTCHA_SLIDER_BTN_SELECTOR)
            if not slider_btn:
                raise RuntimeError("未找到滑块按钮")

            handle_box = await slider_btn.bounding_box()
            if not handle_box:
                raise RuntimeError("无法获取滑块按钮位置")

            # 4. 计算拖拽距离
            track_width = track_box["width"]
            drag_distance = int(track_width * slider_percent / 100.0)
            print(f"    滑块轨道宽度: {track_width:.0f}px")
            print(f"    计算拖拽距离: {drag_distance}px ({slider_percent:.0f}% × {track_width:.0f}px)")

            # 5. 在 iframe 内执行拖拽
            # 滑块起始位置（iframe 内坐标）
            slider_start_x = handle_box["x"] + handle_box["width"] / 2
            slider_start_y = handle_box["y"] + handle_box["height"] / 2

            # 获取 iframe 在页面中的偏移量
            iframe_el = await page.query_selector('iframe')
            iframe_box = await iframe_el.bounding_box()

            # 计算页面绝对坐标
            abs_start_x = iframe_box["x"] + slider_start_x
            abs_start_y = iframe_box["y"] + slider_start_y

            print(f"    iframe偏移: ({iframe_box['x']:.0f}, {iframe_box['y']:.0f})")
            print(f"    页面绝对坐标: ({abs_start_x:.0f}, {abs_start_y:.0f})")

            await execute_drag(page, captcha_frame, abs_start_x, abs_start_y, drag_distance)

            # 6. 等待验证结果
            await asyncio.sleep(3.0)

            # 检查 iframe 是否消失
            for check_wait in [0, 1500, 2000, 2500, 3000]:
                if check_wait > 0:
                    await asyncio.sleep(check_wait / 1000.0)
                still_exists = await find_captcha_iframe(page, timeout=1500)
                if not still_exists:
                    print("  [滑块] 验证通过（iframe 消失）")
                    return True

            # iframe 还在，检查内部状态
            frame = still_exists
            try:
                msg_el = await frame.query_selector(".verify-message-tit")
                if msg_el:
                    msg_text = await msg_el.inner_text()
                    print(f"    验证消息: {msg_text}")
                    if "成功" in msg_text or "通过" in msg_text:
                        print("  [滑块] 验证通过")
                        await asyncio.sleep(1.0)
                        if not await find_captcha_iframe(page, timeout=1500):
                            return True

                fail_text = await frame.evaluate("""() => {
                    const texts = ['验证失败', '拼图未对齐', '请重试', '未匹配', '匹配失败'];
                    for (const t of texts) {
                        if (document.body.innerText.includes(t)) return t;
                    }
                    return '';
                }""")
                if fail_text:
                    print(f"  [滑块] 验证失败: {fail_text}")
                    await asyncio.sleep(attempt * 1.5)
                    continue

                iframe_text = await frame.evaluate("() => document.body.innerText.substring(0, 200)")
                print(f"    iframe内容: {iframe_text}")

            except Exception as check_err:
                print(f"    状态检查异常: {check_err}")

            print("  [滑块] 未检测到明确结果，将重试...")
            await asyncio.sleep(1.0)

        except Exception as e:
            print(f"  [滑块] 异常: {e}")
            if attempt < MAX_CAPTCHA_RETRIES:
                await asyncio.sleep(attempt * 1.5)
            else:
                raise

    raise RuntimeError(f"滑块验证失败（已重试 {MAX_CAPTCHA_RETRIES} 次）")


# ============================================================
# Phase 4: 短信验证码
# ============================================================

async def fill_sms_code(page):
    print("\n[4/5] 等待短信验证码...")

    sms_input = await find_element(page, SMS_INPUT_SELECTORS, timeout=30000)
    if not sms_input:
        raise RuntimeError("短信验证码输入框未出现")

    print("  验证码输入框已就绪")
    print("=" * 50)
    print("  验证码已发送到您的手机")
    print("  请在手机上查看短信，然后在终端输入6位数字验证码")
    print("=" * 50)

    try:
        code = input("\n>>> 请输入验证码: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise RuntimeError("用户取消了验证码输入")

    if not code or not code.isdigit() or len(code) < 4:
        raise ValueError(f"无效的验证码: '{code}'")

    await sms_input.click()
    await asyncio.sleep(random.uniform(0.3, 0.6))

    for digit in code:
        await sms_input.press(digit)
        await asyncio.sleep(random.uniform(0.1, 0.25))

    await asyncio.sleep(random.uniform(0.5, 1.0))
    print(f"  验证码已填写: {'*' * len(code)}")


# ============================================================
# Phase 5: 登录 + 保存状态
# ============================================================

async def click_login_and_save(page, ctx):
    print("\n[5/5] 完成登录...")

    login_btn = await find_element(page, LOGIN_BUTTON_SELECTORS, timeout=15000)
    if not login_btn:
        raise RuntimeError("未找到登录按钮")

    await login_btn.click()
    print("  已点击登录按钮，等待跳转...")

    try:
        await page.wait_for_url("**/mp.toutiao.com/**", timeout=15000)
        await asyncio.sleep(2.0)
    except Exception:
        current_url = page.url
        print(f"  当前 URL: {current_url}")
        if "login" in current_url or "auth" in current_url:
            raise RuntimeError("登录失败：页面仍停留在登录页")

    current_url = page.url
    if "login" in current_url or "auth" in current_url:
        raise RuntimeError("登录可能未成功：仍在登录页")

    await ctx.storage_state(path=str(AUTH_FILE))
    print(f"  登录状态已保存到: {AUTH_FILE}")
    print("  登录成功！")


# ============================================================
# 主流程
# ============================================================

async def toutiao_login(phone_number: str):
    print("=" * 60)
    print("头条号自动登录脚本")
    print(f"手机号: {phone_number[:3]}****{phone_number[-4:]}")
    print(f"登录页: {TOUTIAO_LOGIN_URL}")
    print("=" * 60)
    print()

    playwright = None
    ctx = None

    try:
        playwright, ctx = await launch_browser(USER_DATA_DIR, EDGE_CDP_PORT)

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        print(f"  导航到: {TOUTIAO_LOGIN_URL}")
        await page.goto(TOUTIAO_LOGIN_URL, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(random.uniform(1.5, 3.0))

        await fill_phone_number(page, phone_number)
        captcha_frame = await click_get_code_button(page)
        await solve_captcha_in_frame(page, captcha_frame)
        await fill_sms_code(page)
        await click_login_and_save(page, ctx)

        print("\n" + "=" * 60)
        print("登录流程完成！")
        print(f"登录态文件: {AUTH_FILE}")
        print("=" * 60)

    except Exception as e:
        print(f"\n登录失败: {e}")
        if ctx and ctx.pages:
            await save_screenshot(ctx.pages[0], "login_error")
        raise

    finally:
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="头条号自动登录脚本")
    parser.add_argument("--phone", type=str, help="手机号（11位）")
    args = parser.parse_args()

    phone = args.phone
    if not phone:
        phone = input("请输入手机号: ").strip()

    if not phone.isdigit() or len(phone) != 11:
        print(f"错误: 无效的手机号 '{phone}'")
        sys.exit(1)

    asyncio.run(toutiao_login(phone))


if __name__ == "__main__":
    main()
