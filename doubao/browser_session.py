"""
浏览器会话管理模块
使用本地 Edge 浏览器（替代 CloakBrowser）
"""

import json
import os
import random
import sys
from pathlib import Path
from typing import Optional

from browser_use import Agent, BrowserSession
from browser_use.llm.openai.like import ChatOpenAILike

from browser_utils import get_cdp_url, get_browser_path

# Edge CDP 调试端口
EDGE_CDP_PORT = 9228
CLOAK_CDP_PORT = EDGE_CDP_PORT  # 兼容旧名

# 浏览器用户数据目录
USER_DATA_DIR = Path(__file__).parent.parent / "chrome_profile_doubao"

# 认证文件
AUTH_FILE = Path(__file__).parent / "doubao_auth.json"

# 豆包网址
DOUBAO_URL = "https://www.doubao.com"


# 反检测：覆盖 webdriver / 自动化痕迹的 JS，启动时通过 add_init_script 注入到每个 page
# 这段脚本会在每个文档加载之初执行，先于站点自身的检测脚本
_STEALTH_INIT_SCRIPT = """
// 1. 隐藏 navigator.webdriver（最常见的检测点）
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. 伪造 plugins 长度（无头/自动化浏览器通常 plugins 为空）
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'plugin' }))
});

// 3. 伪造 languages（自动化场景下可能缺失）
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

// 4. 移除 CDP 注入的 cdc_ 变量（Selenium/Puppeteer/Playwright 痕迹）
for (const key of Object.keys(window)) {
    if (key.match(/^cdc_/) || key.match(/^\\$cdc_/)) {
        try { delete window[key]; } catch (e) {}
    }
}

// 5. 覆盖 chrome 对象（自动化下可能缺失 chrome.runtime）
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) { window.chrome.runtime = {}; }

// 6. 让 permissions.query 返回更"正常"的结果
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
}
"""


async def _launch_browser(user_data_dir: Path, cdp_port: int, no_viewport: bool = True, **kwargs):
    """启动本地浏览器（Edge 或 Chrome），返回 (playwright, ctx)。

    浏览器类型由 .env 中 BROWSER_TYPE 决定（edge / chrome），不设置则默认 Edge。

    Args:
        user_data_dir: 浏览器用户数据目录（保存 cookie 与登录态）
        cdp_port: CDP 调试端口，供后续 BrowserSession 通过 CDP 连接
        no_viewport: True 表示不设固定 viewport，窗口尺寸跟随实际显示，避免页面显示不全
    """
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser_path = get_browser_path()
    browser_name = "Chrome" if "chrome" in browser_path.lower() else "Edge"
    print(f"使用 {browser_name}: {browser_path}")

    # 反检测启动参数：屏蔽 Blink 中的自动化标记 + 关闭部分自动化提示
    launch_args = [
        f"--remote-debugging-port={cdp_port}",
        "--disable-blink-features=AutomationControlled",  # 关键：去掉 navigator.webdriver
        "--disable-features=IsolateOrigins,site-per-process",
        "--start-maximized",  # 窗口最大化，配合 no_viewport 让页面正常显示
        "--no-default-browser-check",
        "--no-first-run",
    ]

    launch_kwargs = {
        "user_data_dir": str(user_data_dir),
        "headless": False,
        "executable_path": browser_path,
        "args": launch_args,
        # 不设固定 viewport，让浏览器窗口自然大小决定页面尺寸
        "no_viewport": no_viewport,
        # 排除 enable-automation 开关（默认 Playwright 会带，导致地址栏出现"由自动测试软件控制"提示）
        "ignore_default_args": ["--enable-automation"],
    }

    ctx = await playwright.chromium.launch_persistent_context(**launch_kwargs, **kwargs)

    # 注入 stealth 脚本：必须在每个 page 创建之初执行，覆盖自动化痕迹
    await ctx.add_init_script(_STEALTH_INIT_SCRIPT)

    return playwright, ctx


def get_llm():
    """获取 LLM，支持 LLM_PROVIDER 切换（仅用于非浏览器任务）"""
    api_key = None
    provider = os.getenv("LLM_PROVIDER", "minimax").strip().lower()

    if provider == "minimax":
        api_key = os.getenv("MINIMAX_API_KEY")
        if not api_key:
            print("错误: 请在 .env 文件中设置 MINIMAX_API_KEY")
            sys.exit(1)
        return ChatOpenAILike(
            model="MiniMax-M2.5",
            base_url="https://api.minimaxi.com/v1",
            api_key=api_key,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )
    elif provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("错误: 请在 .env 文件中设置 DEEPSEEK_API_KEY")
            sys.exit(1)
        return ChatOpenAILike(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key=api_key,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )
    else:
        print(f"错误: 未知的 LLM_PROVIDER '{provider}', 支持: minimax, deepseek")
        sys.exit(1)


def get_browser_llm():
    """获取浏览器自动化专用 LLM（固定 DeepSeek，因 MiniMax 不支持 structured output）"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 浏览器自动化需要 DeepSeek LLM，请在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)
    return ChatOpenAILike(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key=api_key,
        dont_force_structured_output=True,
        add_schema_to_system_prompt=True,
    )


async def setup_login():
    """
    打开浏览器让用户手动登录豆包，保存登录状态
    """
    print("正在启动 Edge 浏览器...")
    print(f"请在浏览器中手动登录豆包: {DOUBAO_URL}")
    print("登录完成后, 回到终端按 Enter 保存登录状态...")

    playwright, ctx = await _launch_browser(USER_DATA_DIR, EDGE_CDP_PORT)

    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(DOUBAO_URL)
        input(">>> 登录完成后按 Enter 保存...\n")
    except (EOFError, KeyboardInterrupt):
        pass

    await ctx.storage_state(path=str(AUTH_FILE))
    print(f"登录状态已保存到: {AUTH_FILE}")
    await ctx.close()
    await playwright.stop()
    print("浏览器已关闭")


async def launch_browser_once():
    """
    方案 B：不再由脚本启动浏览器，改为附加到用户手动启动的 Edge（带 CDP 端口）。
    用户需先双击 doubao/start_edge.bat 启动浏览器。

    为兼容旧调用方接口，仍返回 (playwright, ctx) 形状的元组，但两者都为 None；
    实际的浏览器进程由用户手动管理，脚本只通过 CDP 附加。

    Returns:
        (None, None) —— 仅用于占位，调用方不应再依赖这两个对象
    Raises:
        RuntimeError: 当 CDP 端口未就绪时（说明用户尚未启动 Edge）
    """
    print("=" * 50)
    print("方案 B：附加到手动启动的 Edge 浏览器")
    print("=" * 50)
    print(f"CDP 端口: {EDGE_CDP_PORT}")
    print(f"用户数据目录: {USER_DATA_DIR}")
    print()

    # 探测端口；若失败给出明确提示
    try:
        ws_url = get_cdp_url(EDGE_CDP_PORT, max_retries=3)
        print(f"检测到 Edge CDP 服务: {ws_url[:80]}...")
    except Exception as e:
        msg = (
            f"无法连接到 Edge CDP 端口 {EDGE_CDP_PORT}。\n"
            f"请先双击 doubao/start_edge.bat 启动 Edge 浏览器，并在其中登录豆包后再运行本脚本。\n"
            f"原始错误: {e}"
        )
        raise RuntimeError(msg) from e

    # 返回占位值，保持接口兼容（旧代码会对 playwright.stop() 做调用，需在调用方判空）
    return None, None


async def create_browser_session() -> BrowserSession:
    """
    连接到已运行的 Edge 浏览器（通过 CDP）。
    必须在调用 launch_browser_once() 之后使用。

    Returns:
        BrowserSession 实例
    """
    cdp_url = get_cdp_url(EDGE_CDP_PORT)
    print(f"连接到 Edge CDP: {cdp_url}")

    session = BrowserSession(cdp_url=cdp_url)
    await session.connect()

    # 注入反检测脚本
    await _inject_stealth_script(session)

    return session


async def _inject_stealth_script(session: BrowserSession):
    """通过 CDP 注入反检测脚本，覆盖自动化特征"""
    try:
        cdp_session = await session.get_or_create_cdp_session(target_id=None, focus=False)
        stealth_js = """
        // 隐藏 navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // 移除 CDP 注入的 cdc_ 变量
        for (const key of Object.keys(window)) {
            if (key.match(/^cdc_/) || key.match(/^\\$cdc_/)) {
                try { delete window[key]; } catch (e) {}
            }
        }

        // 伪造 chrome 对象
        if (!window.chrome) { window.chrome = {}; }
        if (!window.chrome.runtime) { window.chrome.runtime = {}; }

        // 覆盖 permissions.query
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
        }
        """
        await session.cdp_client.send_raw(
            "Runtime.evaluate",
            {"expression": stealth_js, "returnByValue": False},
            session_id=cdp_session.session_id,
        )
        print("✓ 反检测脚本已注入")
    except Exception as e:
        print(f"⚠ 反检测脚本注入失败: {e}")


async def close_browser(session: BrowserSession):
    """关闭 BrowserSession（不关闭 Edge 进程本身）"""
    if session:
        await session.close()


def _random_behavior_instruction_for_current_page() -> str:
    """生成"已在页面上"场景的随机人类行为指令。

    与 _random_behavior_instruction() 的区别：
    - 不使用"进入页面后"前缀（浏览器已在目标页）
    - 保持相同的元素操作（滚动、鼠标轨迹、翻历史）
    """
    behaviors = [
        "先在页面上随机向下滚动 2-3 屏，停留 3-5 秒，模拟人类浏览行为",
        "在页面上缓慢移动鼠标几次，停留 2-4 秒后再操作，模拟人类鼠标轨迹",
        "上下滚动页面 1-2 次，每次 300-600 像素，模拟人类阅读节奏",
        "先停留 3-6 秒观察页面内容，再进行后续操作",
        "缓慢向下滚动一屏，然后向上回滚一小段，模拟人类反复查看页面",
        "点击左侧的历史对话条目（随便选一个），看看里面的内容，停留 2-3 秒后再返回",
        "上下滚动左侧的历史对话列表，看看之前的对话标题，停留 2-3 秒后再继续操作",
    ]
    return random.choice(behaviors)


def _build_full_chat_task(question: str, behavior: str) -> str:
    """构建完整的 12 步任务指令（含导航步骤，向后兼容）"""
    return f"""你在豆包网站(doubao.com)进行对话测试。请按顺序完成以下步骤：

1. 导航到: {DOUBAO_URL}/chat/
2. {behavior}
3. 等待页面加载完成，确认页面已登录。如果看到登录页面，停止并提示用户重新登录。
4. 点击页面上的"新对话"或"新建对话"按钮开始一个新对话
5. 等待新对话页面加载完成，找到聊天输入框
6. 在输入框中输入以下问题: "{question}"
7. 点击发送按钮（按钮通常在输入框右侧，是一个箭头图标或"发送"文字按钮）
8. 发送后等待 2-5 秒再继续，模拟人类等待回复的节奏
9. 等待助手回复出现。等待时间最多90秒。
10. 等待回复区域完全加载完成，包括所有后续问题建议按钮都出现。
11. 提取助手的完整回复内容（包括所有文字段落，不要截断），以及后续问题的按钮文字。
12. 调用 done 动作时，请把结果作为 JSON 字符串返回，格式为:
    {{"success": true/false, "response": "助手的完整回复内容", "followup_suggestions": ["建议1", "建议2"], "error": "错误信息（若有）"}}

重要提示：
- 每轮都要先点"新对话"开始新对话，再输入问题
- 如果页面出现弹窗、广告或引导，先关闭再继续
- 等待回复时请耐心等待，不要重复点击发送按钮
- 输入框可能是 textarea 或 contenteditable div，找到正确的元素再操作
- 发送按钮可能是 button 元素或 svg 图标，找到可点击的元素
- 提取回复时，确保包含所有文字内容，包括段落换行
- 每个步骤之间保持自然的节奏，不要过于机械
"""


def _build_minimal_chat_task(question: str, behavior: str) -> str:
    """构建精简版任务指令（跳过导航，适用于方案 B）。

    与完整版相比：
    - 去掉导航步骤（浏览器已在 doubao.com/chat/）
    - 随机行为作为第 1 步，先制造多样性
    - 登录检查是"简要检查"而非"停止并提示"
    - 输入和发送合为一步
    """
    return f"""你在豆包网站(doubao.com/chat/)进行对话测试，浏览器已经在此页面。请按顺序完成以下步骤：

1. {behavior}
2. 确认页面已登录（简要检查）。如果看到登录提示，等待页面自动恢复登录态。
3. 点击页面上的"新对话"或"新建对话"按钮开始一个新对话
4. 在输入框中输入以下问题: "{question}"，然后点击发送按钮
5. 等待助手回复出现（最多90秒），等待回复区域完全加载完成，包括所有后续问题建议按钮都出现。
6. 提取助手的完整回复内容，调用 done 动作时把结果作为 JSON 字符串返回，格式为:
    {{"success": true/false, "response": "助手的完整回复内容", "followup_suggestions": ["建议1", "建议2"], "error": "错误信息（若有）"}}

重要提示：
- 浏览器已在目标页面，不需要导航
- 每轮都先点"新对话"开始新对话，再输入问题
- 如果页面出现弹窗、广告或引导，先关闭再继续
- 等待回复时请耐心等待，不要重复点击发送按钮
- 提取回复时，确保包含所有文字内容，包括段落换行
- 每个步骤之间保持自然的节奏，不要过于机械
"""


def build_chat_task(question: str, skip_nav: bool = False) -> str:
    """
    构建与豆包对话的任务指令
    每轮都新建对话：导航 → 点新对话 → 输入 → 发送 → 提取回复

    Args:
        question: 要发送的问题
        skip_nav: True 表示浏览器已在目标页面，跳过导航步骤（方案 B 推荐）

    Returns:
        Agent 任务指令字符串
    """
    if skip_nav:
        behavior = _random_behavior_instruction_for_current_page()
        return _build_minimal_chat_task(question, behavior)
    else:
        behavior = _random_behavior_instruction()
        return _build_full_chat_task(question, behavior)


async def send_question(
    session: BrowserSession,
    question: str,
    max_steps: int = 50,
    skip_nav: bool = False,
) -> tuple[str, int]:
    """
    通过 Agent 发送问题并获取回复（每轮都新建对话）

    Args:
        skip_nav: True 跳过导航步骤（方案 B 推荐，浏览器已在目标页面）

    Returns:
        (回复内容, 响应时间毫秒)
    """
    import time

    llm = get_browser_llm()
    task = build_chat_task(question, skip_nav=skip_nav)

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        use_vision=False,
        max_actions_per_step=5,
        max_failures=10,
        step_timeout=180,
    )

    start_time = time.time()

    try:
        history = await agent.run(max_steps=max_steps)
        elapsed_ms = int((time.time() - start_time) * 1000)

        if history and history.final_result():
            final = history.final_result()
            try:
                parsed = json.loads(final) if isinstance(final, str) else final
                if isinstance(parsed, dict) and parsed.get("success"):
                    response = parsed.get("response", "")
                    # 如果有后续问题建议，附加到回复中
                    followups = parsed.get("followup_suggestions", [])
                    if followups:
                        response += "\n\n[后续建议: " + ", ".join(followups) + "]"
                    return response, elapsed_ms
                elif isinstance(parsed, dict) and parsed.get("error"):
                    return f"Error: {parsed.get('error')}", elapsed_ms
            except (json.JSONDecodeError, TypeError):
                pass
            return str(final), elapsed_ms

        return "", elapsed_ms

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return f"Exception: {str(e)}", elapsed_ms
