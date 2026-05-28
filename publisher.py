#!/usr/bin/env python3
"""
头条号自动发文 Demo
基于 browser-use + DeepSeek 实现 AI 驱动的浏览器自动化发文

用法:
  python publisher.py --setup
  python publisher.py --publish --title "标题" --content "正文"
  python publisher.py --publish --title "标题" --content-file article.txt
  python publisher.py --publish --title "标题" --content-file article.txt --images cover.jpg body1.jpg body2.jpg
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

# Windows 控制台默认 GBK 编码，设为 UTF-8 支持 emoji 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from dotenv import load_dotenv

from toutiao_uploader import upload_images

load_dotenv()

from browser_use import Agent, BrowserSession
from browser_use.llm.openai.like import ChatOpenAILike

from browser_utils import get_cdp_url, get_browser_path

AUTH_FILE = Path(__file__).parent / "auth.json"
TOUTIAO_HOME = "https://mp.toutiao.com"
TOUTIAO_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
USER_DATA_DIR = Path(__file__).parent / "chrome_profile"

# Edge CDP 调试端口（单用户模式默认值）
EDGE_CDP_PORT = 9227

# 兼容旧常量名
CLOAK_CDP_PORT = EDGE_CDP_PORT

# 全局 UserManager 实例
_user_manager: Optional["UserManager"] = None


def get_user_manager() -> "UserManager":
    """获取全局 UserManager 实例（延迟加载）"""
    global _user_manager
    if _user_manager is None:
        from user_manager import UserManager
        _user_manager = UserManager.get_instance()
    return _user_manager


async def _launch_browser(user_data_dir: Path, cdp_port: int, viewport=None,
                          fresh_profile: bool = False, auth_file: Path = None, **kwargs):
    """启动本地浏览器（Edge 或 Chrome），返回 (playwright, ctx, temp_dir)。

    fresh_profile=True 时，创建临时目录作为 profile，并从 auth_file 注入 cookies。
    temp_dir 为非 None 时表示需要清理的临时目录路径。
    """
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser_path = get_browser_path()
    browser_name = "Chrome" if "chrome" in browser_path.lower() else "Edge"
    print(f"使用 {browser_name}: {browser_path}")

    temp_dir = None
    if fresh_profile:
        temp_dir = tempfile.mkdtemp(prefix="browser_fresh_")
        user_data_dir = Path(temp_dir)
        print(f"新建临时 profile: {temp_dir}")

    launch_kwargs = {
        "user_data_dir": str(user_data_dir),
        "headless": False,
        "executable_path": browser_path,
        "args": [f"--remote-debugging-port={cdp_port}"],
    }
    if viewport:
        launch_kwargs["viewport"] = viewport

    ctx = await playwright.chromium.launch_persistent_context(**launch_kwargs, **kwargs)

    # fresh_profile 时，从 auth_file 注入 cookies（launch_persistent_context 不支持 storage_state）
    if fresh_profile and auth_file and auth_file.exists():
        try:
            with open(auth_file, "r", encoding="utf-8") as f:
                auth_data = json.load(f)
            cookies = auth_data.get("cookies", [])
            if cookies:
                await ctx.add_cookies(cookies)
                print(f"从 {auth_file} 注入 {len(cookies)} 个 cookie 到临时 profile")
            else:
                print(f"警告: {auth_file} 中没有 cookie")
        except Exception as e:
            print(f"警告: 注入 cookies 失败: {e}")

    return playwright, ctx, temp_dir


def get_llm():
    """获取 LLM，支持 LLM_PROVIDER 切换（用于非浏览器任务）"""
    provider = os.getenv("LLM_PROVIDER", "mimo").strip().lower()

    if provider == "mimo":
        api_key = os.getenv("MIMO_API_KEY")
        if not api_key:
            print("错误: 请在 .env 文件中设置 MIMO_API_KEY")
            sys.exit(1)
        return ChatOpenAILike(
            model="mimo-v2-pro",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            api_key=api_key,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )
    elif provider == "minimax":
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
        print(f"错误: 未知的 LLM_PROVIDER '{provider}', 支持: mimo, minimax, deepseek")
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




async def setup_login_cloak(
    auth_file: Path = AUTH_FILE,
    user_data_dir: Path = USER_DATA_DIR,
    cdp_port: int = EDGE_CDP_PORT,
):
    """
    使用本地 Edge 打开浏览器，用户手动登录后按 Enter 保存 storage_state。

    auth_file: 登录状态保存路径
    user_data_dir: 浏览器用户数据目录
    cdp_port: CDP 调试端口
    """
    print("正在启动 Edge 浏览器...")
    print(f"请在浏览器中手动登录头条号: {TOUTIAO_HOME}")
    print("登录完成后, 回到终端按 Enter 保存登录状态...")
    print()

    playwright, ctx = await _launch_browser(user_data_dir, cdp_port)
    try:
        cdp_url = get_cdp_url(cdp_port)

        session = BrowserSession(cdp_url=cdp_url)
        await session.connect()
        page = await session.new_page()
        await page.goto(TOUTIAO_HOME)

        try:
            input(">>> 登录完成后按 Enter 保存...")
        except (EOFError, KeyboardInterrupt):
            pass

        await ctx.storage_state(path=str(auth_file))
        print(f"登录状态已保存到: {auth_file}")
    finally:
        await ctx.close()
        await playwright.stop()


RESULT_DIR = Path(__file__).parent / "result"


def get_account_from_auth(auth_file: Path = AUTH_FILE) -> str:
    """从 auth.json cookies 中尝试提取头条账号标识 (uid_tt 或 sessionid)。"""
    try:
        with open(auth_file, "r", encoding="utf-8") as f:
            auth_data = json.load(f)
        for c in auth_data.get("cookies", []):
            if c.get("name") in ("uid_tt", "uid_tt_ss"):
                return c.get("value", "")
    except Exception:
        pass
    return ""


def write_result(record: dict):
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    ts = record.get("operation_time", datetime.now().strftime("%Y%m%d_%H%M%S"))
    safe_ts = ts.replace(":", "").replace(" ", "_")
    out = RESULT_DIR / f"publish_{safe_ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"结果已写入: {out}")


def is_markdown_content(content: str) -> bool:
    """检测内容是否包含 Markdown 语法特征（需满足至少2个特征）"""
    import re
    indicators = [
        bool(re.match(r'^#{1,6}\s+', content, re.MULTILINE)),  # headings
        bool(re.search(r'\*\*[^*]+\*\*', content)),             # bold
        bool(re.search(r'!\[[^\]]*\]\([^)]+\)', content)),       # images
        bool(re.search(r'\[[^\]]+\]\([^)]+\)', content)),        # links
        bool(re.match(r'^[-*+]\s+', content, re.MULTILINE)),     # list items
        bool(re.match(r'^>\s+', content, re.MULTILINE)),         # blockquotes
        bool(re.search(r'^---$', content, re.MULTILINE)),        # hr
        bool(re.search(r'`[^`]+`', content)),                     # inline code
    ]
    return sum(1 for ind in indicators if ind) >= 2


async def publish_article_internal(
    title: str,
    content: str,
    image_paths: Optional[List[str]] = None,
    auth_file: Path = AUTH_FILE,
    user_data_dir: Path = USER_DATA_DIR,
    cdp_port: int = CLOAK_CDP_PORT,
    fresh_profile: bool = False,
) -> dict:
    """
    内部发布函数，参数化所有用户相关资源。

    image_paths: 要上传的图片文件路径列表（第一张作为封面）。
    auth_file: 登录状态文件路径
    user_data_dir: 浏览器用户数据目录
    cdp_port: CDP 调试端口
    """
    operation_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    account = get_account_from_auth(auth_file)
    result = {
        "operation_time": operation_time,
        "account": account,
        "success": False,
        "failure_reason": "",
        "article_title": title,
        "article_url": "",
    }

    if not auth_file.exists():
        msg = f"未找到登录状态文件 {auth_file}, 请先运行 --setup 登录"
        print(f"错误: {msg}")
        result["failure_reason"] = msg
        return result

    # 验证 auth.json 中包含头条号 cookie
    try:
        with open(auth_file, "r", encoding="utf-8") as f:
            auth_data = json.load(f)
        cookies = auth_data.get("cookies", [])
        toutiao_domains = [".toutiao.com", "mp.toutiao.com", "www.toutiao.com"]
        has_toutiao_cookie = any(
            c.get("domain", "").rstrip(".") in toutiao_domains
            or c.get("domain", "").lstrip(".") in toutiao_domains
            for c in cookies
        )
        if not has_toutiao_cookie:
            msg = f"{auth_file} 中未检测到头条号(toutiao.com)的 cookie，请重新运行 --setup 登录"
            print(f"警告: {msg}")
            result["failure_reason"] = msg
            return result
        if not cookies:
            msg = f"{auth_file} 中不包含任何 cookie，请重新运行 --setup 登录"
            print(f"错误: {msg}")
            result["failure_reason"] = msg
            return result
    except json.JSONDecodeError:
        msg = f"{auth_file} 格式无效(不是合法的 JSON)，请重新运行 --setup 登录"
        print(f"错误: {msg}")
        result["failure_reason"] = msg
        return result

    # 上传图片并获取 URL
    cover_local_path = None
    body_image_urls = []
    if image_paths:
        cover_local_path = image_paths[0]
        remaining = image_paths[1:]
        if remaining:
            uploaded_urls = await upload_images(str(auth_file), remaining)
            if uploaded_urls:
                body_image_urls = uploaded_urls
                for i, url in enumerate(body_image_urls):
                    print(f"正文图片 {i+1}: {url}")

    llm = get_browser_llm()

    session = None
    playwright = None
    temp_dir = None
    try:
        playwright, ctx, temp_dir = await _launch_browser(
            user_data_dir,
            cdp_port,
            viewport={"width": 1440, "height": 1000},
            fresh_profile=fresh_profile,
            auth_file=auth_file,
        )
        cdp_url = get_cdp_url(cdp_port)
        print(f"Edge CDP: {cdp_url}")
        session = BrowserSession(cdp_url=cdp_url)
        await session.connect()

        # 检测是否为 Markdown 格式
        is_markdown = is_markdown_content(content)
        if is_markdown:
            print(f"检测到 Markdown 格式，将使用 markdowntorichtext.com 转换为富文本")

        # 构建正文图片指令
        body_image_instruction = ""
        if body_image_urls:
            body_image_instruction = f"""
   - 在正文中适当位置插入以下图片（使用编辑器的图片按钮或 file input 上传，或直接用 HTML <img> 标签）:
"""
            for i, url in enumerate(body_image_urls):
                body_image_instruction += f'     图片{i+1} URL: {url}\n'
        else:
            body_image_instruction = "\n"

        # 构建封面设置指令
        if cover_local_path:
            abs_cover_path = str(Path(cover_local_path).absolute())
            cover_instruction = f"""
7. 设置封面图片（严格按顺序）：
   - 第1步：在封面设置区域，找到并点击那个"+"号/加号图标（通常是一个虚线框内的加号）
   - 第2步：点击后弹出一个图片选择对话框，在对话框中找到并点击"本地上传"或"上传图片"按钮
   - 第3步：此时页面会出现隐藏的 file input 元素（type="file"）
   - 第4步：使用 file input 上传这个本地文件：
     {abs_cover_path}
   - 第5步：上传完成后等待封面预览显示，再关闭对话框
"""
        else:
            cover_instruction = """
7. 检查封面设置。如果系统已自动匹配封面，可以跳过。如需设置封面，选择"无封面"。
"""

        if is_markdown:
            # Markdown 格式：先转换富文本，再粘贴到头条号
            task = f"""请按顺序完成以下步骤来发布一篇文章：

【第一阶段：Markdown 转富文本】
1. 首先导航到: https://markdowntorichtext.com/zh/
2. 等待页面加载完成。
3. 找到页面的 Markdown 输入区域（通常是一个大的 textarea 或 contenteditable 区域）。
4. 使用以下方法将 Markdown 内容粘贴到输入区域：
   - 使用 evaluate 执行 JavaScript：
     const textarea = document.querySelector('textarea') || document.querySelector('[contenteditable]');
     if (textarea) {{ textarea.value = `{content}`.slice(0, 50000); textarea.dispatchEvent(new Event('input', {{ bubbles: true }})); }}
   - 如果上述方法不行，尝试用 evaluate 直接设置输入区域的 value
5. 等待页面渲染预览（右侧或下方应显示格式化后的预览）。
6. 找到页面上的"复制"按钮（Copy / Copy rich text / 复制按钮），点击它。
   - 如果页面上有多个复制按钮，尝试点击那个复制富文本的按钮。
   - 复制成功后，页面上通常会有"已复制"或"Rich text copied to clipboard"的提示。
7. 确认剪贴板中已有富文本内容。

【第二阶段：发布到头条号】
8. 导航到头条号后台: {TOUTIAO_HOME}
9. 确认已登录后台。如果看到登录页面，停止并提示用户重新 --setup 登录。
   - 在首页/后台读取当前登录账号的显示名（通常在页面右上角头像旁边，或个人中心，例如"头条号名称"/昵称），记录为 account_name。
10. 导航到发布文章页面: {TOUTIAO_PUBLISH_URL}
11. 等待页面加载完成。
12. 找到文章标题输入框，输入标题: "{title}"
13. 找到文章正文编辑区域（通常是富文本编辑器 body.contenteditable）。
14. 使用以下方法粘贴富文本内容：
    - 点击编辑区域获取焦点
    - 使用 evaluate 执行 JavaScript 粘贴富文本（模拟粘贴 HTML）：
      const editor = document.querySelector('[contenteditable=true]');
      if (editor) {{
        editor.focus();
        // 使用 execCommand 插入 HTML（模拟 Ctrl+V 粘贴富文本）
        document.execCommand('insertHTML', false, `从剪贴板获取的富文本HTML`);
      }}
    - 如果上述方法不行，尝试先导航回 markdowntorichtext.com 页面，手动 Ctrl+A 全选富文本预览区域，Ctrl+C 复制，然后回到头条号编辑器 Ctrl+V 粘贴{body_image_instruction}
15. 等待编辑器渲染，确认内容已正确显示（标题、段落、加粗、图片等）。
16. {cover_instruction.strip()}
17. 检查分类等必填项是否缺失。
18. 找到页面底部的"预览并发布"按钮（它是浮动固定在页面底部的）。
    - 如果看不到该按钮，尝试缩小页面或等待页面加载完成。
19. 点击"预览并发布"按钮。等待预览对话框弹出，然后点击对话框中的"确认发布"按钮。
    - **重要**：点击"确认发布"后，无论页面显示什么（包括"草稿保存中"、无明显变化），
      都**立即调用 done 并标记 success=true**，不要再回到编辑页或重新输入内容。
    - 最多尝试 2 次"预览并发布"→"确认发布"。2 次后无论结果如何都调用 done。
    - 只有出现明确的错误提示（红色Toast、弹窗报错、登录页）时，才标记 success=false。
20. 调用 done 动作时，请把最终结果作为 JSON 字符串返回，格式为:
    {{"success": true/false, "account_name": "步骤9读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}

重要提示：
- 头条号编辑器是 contenteditable 的富文本区域，可能需要先点击编辑区域获取焦点再粘贴。
- 遇到弹窗/引导/广告先关闭再继续。
- 每个步骤完成后确认页面响应再继续下一步。
- 发布按钮是浮动固定在页面底部的，如果看不到，尝试等待或缩小页面。
- 设置封面时：先点封面区域的"+"加号 → 弹出对话框后点"本地上传" → 使用 file input 上传
- 预览对话框出现后，寻找文字为"确认发布"的按钮（不是"草稿已保存"），仔细找到正确的按钮并点击。
- 预览对话框中，"确认发布"按钮通常在对话框底部，请耐心滚动或查找。
- **点击"确认发布"后立即停止，不要反复搜索内容管理页或重新发布。**
- Markdown 转换网站：https://markdowntorichtext.com/zh/
- 如果复制按钮不起作用，尝试点击页面上的 Copy 或 "Copy rich text" 按钮。
"""
        else:
            # 纯文本格式：原有流程
            task = f"""你在头条号后台(mp.toutiao.com)操作。请按顺序完成以下步骤来发布一篇文章：

1. 首先导航到: {TOUTIAO_HOME}
2. 确认已登录后台。如果看到登录页面，停止并提示用户重新 --setup 登录。
   - 在首页/后台读取当前登录账号的显示名（通常在页面右上角头像旁边，或个人中心，例如"头条号名称"/昵称），记录为 account_name。
3. 导航到发布文章页面: {TOUTIAO_PUBLISH_URL}
4. 等待页面加载完成。
5. 找到文章标题输入框，输入标题: "{title}"
6. 找到文章正文编辑区域（通常是富文本编辑器 body.contenteditable），输入以下内容:{body_image_instruction}

<content>
{content}
</content>{cover_instruction}
8. 检查分类等必填项是否缺失。
9. 找到页面底部的"预览并发布"按钮（它是浮动固定在页面底部的）。
   - 如果看不到该按钮，尝试缩小页面或等待页面加载完成。
10. 点击"预览并发布"按钮。等待预览对话框弹出，然后点击对话框中的"确认发布"按钮。
    - **重要**：点击"确认发布"后，无论页面显示什么（包括"草稿保存中"、无明显变化），
      都**立即调用 done 并标记 success=true**，不要再回到编辑页或重新输入内容。
    - 最多尝试 2 次"预览并发布"→"确认发布"。2 次后无论结果如何都调用 done。
    - 只有出现明确的错误提示（红色Toast、弹窗报错、登录页）时，才标记 success=false。
11. 调用 done 动作时，请把最终结果作为 JSON 字符串返回，格式为:
    {{"success": true/false, "account_name": "步骤2读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}

重要提示：
- 头条号编辑器是 contenteditable 的富文本区域，可能需要先点击编辑区域获取焦点再输入。
- 遇到弹窗/引导/广告先关闭再继续。
- 每个步骤完成后确认页面响应再继续下一步。
- 发布按钮是浮动固定在页面底部的，如果看不到，尝试等待或缩小页面。
- 设置封面时：先点封面区域的"+"加号 → 弹出对话框后点"本地上传" → 使用 file input 上传
- 预览对话框出现后，寻找文字为"确认发布"的按钮（不是"草稿已保存"），仔细找到正确的按钮并点击。
- 预览对话框中，"确认发布"按钮通常在对话框底部，请耐心滚动或查找。
- **点击"确认发布"后立即停止，不要反复搜索内容管理页或重新发布。**
"""

        available_files = []
        if cover_local_path:
            available_files.append(str(Path(cover_local_path).absolute()))

        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            use_vision=False,
            max_actions_per_step=3,
            max_failures=5,
            step_timeout=120,
            available_file_paths=available_files if available_files else None,
        )

        print(f"开始发布文章: {title}")
        print("观察浏览器窗口查看 Agent 操作过程...\n")

        history = await agent.run(max_steps=80)

        try:
            final = history.final_result()
            if final:
                print(f"结果: {final}")
                try:
                    parsed = json.loads(final) if isinstance(final, str) else final
                    if isinstance(parsed, dict):
                        if "success" in parsed:
                            result["success"] = bool(parsed.get("success"))
                        if parsed.get("account_name"):
                            result["account"] = parsed["account_name"]
                        if parsed.get("article_url"):
                            result["article_url"] = parsed["article_url"]
                        if parsed.get("failure_reason"):
                            result["failure_reason"] = parsed["failure_reason"]
                except (json.JSONDecodeError, TypeError):
                    pass

            if history.is_successful():
                print(f"\n文章发布流程完成!")
                print(f"执行步骤数: {len(history.history)}")
                if not result["failure_reason"]:
                    result["success"] = True
            else:
                print(f"\n发布可能未成功")
                errors = history.errors() or []
                err_msgs = [str(e) for e in errors if e]
                if err_msgs and not result["failure_reason"]:
                    result["failure_reason"] = "; ".join(err_msgs)
                if not result["failure_reason"]:
                    result["failure_reason"] = "Agent 未明确报告成功"
        except AttributeError:
            print(f"\n执行完成（具体状态无法解析，请检查浏览器窗口）")
            if not result["failure_reason"]:
                result["failure_reason"] = "无法解析 Agent 执行状态"
    except Exception as e:
        result["failure_reason"] = f"运行异常: {e}"
        print(f"运行异常: {e}")
    finally:
        if session:
            await session.close()
        if playwright:
            await playwright.stop()
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"临时 profile 已清理: {temp_dir}")
        print("浏览器已关闭.")

    return result


async def publish_article(title: str, content: str, image_paths: Optional[List[str]] = None) -> dict:
    """
    向后兼容的单用户发布函数，内部调用 publish_article_internal。
    """
    return await publish_article_internal(
        title=title,
        content=content,
        image_paths=image_paths,
        auth_file=AUTH_FILE,
        user_data_dir=USER_DATA_DIR,
        cdp_port=CLOAK_CDP_PORT,
    )


TEST_PERPER_DIR = Path(__file__).parent / "test_perper"


def _mark_published(result: dict, article_dir: str | None):
    """发布成功后，重命名文章文件夹标记为已发布。"""
    if not result.get("success"):
        return
    if not article_dir:
        return
    target = Path(article_dir)
    if not target.exists():
        return
    base_name = target.name.rstrip("/\\")
    # 如果已经是 perperXXX_N 格式，说明已标记过，跳过
    import re
    if re.search(r'_\d+$', base_name):
        return
    # 计算新的编号
    parent = target.parent
    publish_count = len([
        d for d in parent.iterdir()
        if d.is_dir() and d.name.startswith(base_name + "_")
    ])
    new_name = f"{base_name}_{publish_count + 1}"
    new_path = target.rename(parent / new_name)
    print(f"\n已标记文章为已发布: {base_name} → {new_name}")


async def auto_publish_from_local(args):
    """扫描 test_perper 目录，自动选取未发布的文章发布，成功后标记为已发布。"""
    if not TEST_PERPER_DIR.exists():
        print(f"错误: 找不到 test_perper 目录: {TEST_PERPER_DIR}")
        return

    # 扫描所有 perper 开头的文件夹，排除已发布的（名称带 _N 后缀）
    import re
    folders = sorted([
        d for d in TEST_PERPER_DIR.iterdir()
        if d.is_dir() and d.name.startswith("perper")
    ])

    if not folders:
        print("test_perper 下没有任何文章文件夹")
        return

    # 分离已发布和未发布
    unpublished = []
    published = []
    for folder in folders:
        # 已发布的文件夹名格式: perper001_1, perper002_2 等
        if re.search(r'_\d+$', folder.name):
            published.append(folder)
        else:
            unpublished.append(folder)

    print(f"test_perper 共 {len(folders)} 个文件夹:")
    print(f"  未发布: {len(unpublished)} 个 {[f.name for f in unpublished]}")
    print(f"  已发布: {len(published)} 个 {[f.name for f in published]}")
    print()

    if not unpublished:
        print("所有文章均已发布，无需继续")
        return

    # 选取第一个未发布的文件夹
    target_dir = unpublished[0]
    print(f"选取文章: {target_dir.name}")

    # 读取 article.txt
    article_file = target_dir / "article.txt"
    if not article_file.exists():
        print(f"错误: 找不到文章文件: {article_file}")
        return

    content = article_file.read_text(encoding="utf-8").strip()
    if not content:
        print(f"错误: 文章内容为空: {article_file}")
        return

    # 提取标题（第一行），清理 markdown 标记
    lines = content.split('\n')
    title = lines[0].strip().lstrip('#').strip()
    # 如果第一行是空行或与第二行之间有空行，尝试用第二行做标题
    body_start = 1
    if len(lines) > 1 and lines[1].strip() == '':
        # 标题后面有空行，标题就是第一行
        body_start = 2
    body = '\n'.join(lines[body_start:]).strip()

    if not body:
        # 如果分离不出正文，整段作为正文
        body = content

    print(f"  标题: {title}")
    print(f"  字数: {len(body)}")

    # 查找图片文件
    image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    image_paths = []
    for f in sorted(target_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in image_extensions:
            image_paths.append(str(f))

    if image_paths:
        print(f"  图片: {len(image_paths)} 张 {[Path(p).name for p in image_paths]}")
        print(f"  封面: {Path(image_paths[0]).name}")
    else:
        print("  图片: 无")

    print()

    # 发布文章
    if args.user_id:
        try:
            um = get_user_manager()
        except FileNotFoundError:
            print("错误: 未找到 users.yaml 配置文件")
            return
        user = um.get_user(args.user_id)
        if not user:
            print(f"错误: 未知用户 {args.user_id}")
            return

        from concurrent_publisher import ConcurrentPublisher
        publisher = ConcurrentPublisher(um)
        result = await publisher.publish_for_user(
            user_id=args.user_id,
            title=title,
            content=body,
            image_paths=image_paths if image_paths else None,
        )
        write_result(result)
    else:
        result = await publish_article(title, body, image_paths if image_paths else None)
        write_result(result)

    # 发布成功后，重命名文件夹标记为已发布
    if result.get("success"):
        # 计算新的文件夹名: perperXXX_N
        base_name = re.match(r'(perper\d+)', target_dir.name).group(1)
        # 统计该基础名称下已发布的数量
        publish_count = len([
            d for d in TEST_PERPER_DIR.iterdir()
            if d.is_dir() and d.name.startswith(base_name + "_")
        ])
        new_name = f"{base_name}_{publish_count + 1}"
        new_path = target_dir.rename(TEST_PERPER_DIR / new_name)
        print(f"\n文章发布成功，已标记为已发布: {target_dir.name} → {new_name}")
    else:
        print(f"\n文章发布失败，未标记文件夹: {result.get('error', '未知错误')}")


async def main():
    parser = argparse.ArgumentParser(description="头条号自动发文 Demo (browser-use + DeepSeek)")
    parser.add_argument("--setup", action="store_true", help="首次: 打开浏览器手动登录, 保存登录状态")
    parser.add_argument("--publish", action="store_true", help="使用已保存的登录状态自动发文")
    parser.add_argument("--title", type=str, help="文章标题")
    parser.add_argument("--content", type=str, help="文章正文")
    parser.add_argument("--content-file", type=str, help="从文件读取文章正文")
    parser.add_argument("--images", nargs="+", help="文章配图（第一张作为封面，其余插入正文）")
    parser.add_argument("--user-id", type=str, help="指定用户 ID（多用户模式，需先配置 users.yaml）")
    parser.add_argument("--list-users", action="store_true", help="列出所有已配置用户")
    parser.add_argument("--generate-from-hot", action="store_true", help="从头条热点自动生成文章并发布")
    parser.add_argument("--generate-only", action="store_true", help="从头条热点自动生成文章但不发布（保存到 test_perper）")
    parser.add_argument("--topic-index", type=int, default=0, help="热点话题索引（0-based，默认0）")
    parser.add_argument("--topic-count", type=int, default=20, help="获取热点话题数量（默认20）")
    parser.add_argument("--auto-publish-local", action="store_true", help="从 test_perper 自动选取未发布文章并发布")
    parser.add_argument("--fresh-profile-count", type=int, help="使用新建 Chrome profile 逐个发布 N 篇未发布文章（测试反AI检测）")

    args = parser.parse_args()

    # 全新 profile 顺序发布：测试反AI检测
    if args.fresh_profile_count:
        if not args.user_id:
            print("错误: --fresh-profile-count 需要配合 --user-id 指定用户")
            sys.exit(1)
        from hot_topic_generator import save_used_topic
        from concurrent_publisher import ConcurrentPublisher

        try:
            um = get_user_manager()
        except FileNotFoundError:
            print("错误: 未找到 users.yaml 配置文件")
            return
        user = um.get_user(args.user_id)
        if not user:
            print(f"错误: 未知用户 {args.user_id}")
            return

        publisher = ConcurrentPublisher(um)

        # 扫描未发布文章
        import re as _re
        all_folders = sorted([
            d for d in TEST_PERPER_DIR.iterdir()
            if d.is_dir() and d.name.startswith("perper")
        ])
        base_map = {}
        for d in all_folders:
            m = _re.match(r'^(perper\d+)(?:_(\d+))?$', d.name)
            if not m:
                continue
            base = m.group(1)
            ver = int(m.group(2)) if m.group(2) else 999
            if base not in base_map or ver > base_map[base][1]:
                base_map[base] = (d, ver)

        # 读取已发布标题
        topics_used_file = TEST_PERPER_DIR / "topics_used.json"
        used_titles = set()
        if topics_used_file.exists():
            try:
                used_data = json.loads(topics_used_file.read_text(encoding="utf-8"))
                for item in used_data:
                    used_titles.add(item.get("article_title", "").strip())
            except Exception:
                pass

        # 选取未发布文章
        unpub = []
        for base, (folder, ver) in sorted(base_map.items()):
            # 支持 .md 和 .txt 文件
            art = folder / "article.md" if (folder / "article.md").exists() else folder / "article.txt"
            if not art.exists():
                continue
            content = art.read_text(encoding="utf-8").strip()
            title = content.split('\n')[0].lstrip('#').strip()
            if title not in used_titles:
                unpub.append(folder)

        if not unpub:
            print("没有未发布的文章")
            return

        count = min(args.fresh_profile_count, len(unpub))
        print(f"=== 全新 Profile 发布测试 ===")
        print(f"用户: {args.user_id} ({user.name})")
        print(f"计划发布 {count} 篇（共 {len(unpub)} 篇未发布）")
        print()

        for i in range(count):
            folder = unpub[i]
            # 支持 .md 和 .txt 文件
            art = folder / "article.md"
            if not art.exists():
                art = folder / "article.txt"
            if not art.exists():
                print(f"错误: {folder.name} 下找不到 article.md 或 article.txt")
                continue
            art_content = art.read_text(encoding="utf-8").strip()
            lines = art_content.split('\n')
            art_title = lines[0].lstrip('#').strip()
            body_start = 2 if len(lines) > 1 and lines[1].strip() == '' else 1
            body = '\n'.join(lines[body_start:]).strip() or art_content
            file_type = "md" if art.suffix == '.md' else "txt"

            # 获取图片
            image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
            img_paths = [str(f) for f in sorted(folder.iterdir()) if f.is_file() and f.suffix.lower() in image_extensions]

            print(f"\n{'='*50}")
            print(f"[{i+1}/{count}] 发布: {folder.name} [{file_type.upper()}]")
            print(f"  标题: {art_title}")
            md_tag = " [Markdown→富文本]" if is_markdown_content(body) else ""
            print(f"  字数: {len(body)}字, 图片: {len(img_paths)}张{md_tag}")
            print(f"{'='*50}\n")

            result = await publisher.publish_for_user(
                user_id=args.user_id,
                title=art_title,
                content=body,
                image_paths=img_paths if img_paths else None,
                fresh_profile=True,
            )

            ok = "✅ 成功" if result.get("success") else "❌ 失败"
            print(f"\n  结果: {ok}")
            if result.get("article_url"):
                print(f"  URL: {result['article_url']}")
            if result.get("failure_reason"):
                print(f"  原因: {result['failure_reason'][:150]}")

            if result.get("success"):
                # 更新 topics_used.json
                used_data = []
                if topics_used_file.exists():
                    try:
                        used_data = json.loads(topics_used_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                used_data.append({
                    "topic_title": art_title,
                    "article_title": art_title,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                topics_used_file.write_text(json.dumps(used_data, ensure_ascii=False, indent=2), encoding="utf-8")

                # 重命名文件夹
                base = folder.name
                publish_count = len([
                    d for d in TEST_PERPER_DIR.iterdir()
                    if d.is_dir() and d.name.startswith(base + "_")
                ])
                new_name = f"{base}_{publish_count + 1}"
                folder.rename(TEST_PERPER_DIR / new_name)
                print(f"  已标记: {base} → {new_name}")

        print(f"\n{'='*50}")
        print(f"全部完成: {count} 篇")
        return

    # 本地文章自动发布：扫描 test_perper → 选未发布文件夹 → 发布 → 标记已发布
    if args.auto_publish_local:
        from hot_topic_generator import load_existing_articles
        await auto_publish_from_local(args)
        return

    # 热点自动生成：抓取头条热榜 → LLM 生成文章 → 注入 publish 流程
    if args.generate_from_hot or args.generate_only:
        print("正在获取头条热榜...（自动过滤敏感话题）")
        from hot_topic_generator import HotTopicFetcher, ArticleGenerator

        fetcher = HotTopicFetcher()
        try:
            # 传入 LLM 用于语义过滤和文章生成（使用 LLM_PROVIDER 指定的模型，默认 mimo）
            llm = get_llm()
            topics = await fetcher.fetch(
                count=max(args.topic_count, args.topic_index + 1),
                llm=llm,
            )
        except Exception as e:
            print(f"获取热点失败: {e}")
            return

        if not topics:
            print("未获取到任何热点话题")
            return

        # 找到第一个非重复的热点话题
        from hot_topic_generator import load_existing_articles, is_topic_duplicate
        existing_articles = load_existing_articles()
        if existing_articles:
            print(f"  本地已有 {len(existing_articles)} 篇文章，检查话题是否重复...")

        topic = None
        for t in topics:
            dup, matched_title = is_topic_duplicate(t["title"])
            if dup:
                print(f"  跳过重复话题: {t['title']} (与已有文章「{matched_title}」重复)")
                continue
            topic = t
            break

        if not topic:
            print("所有热点话题均已生成过文章，无法继续")
            return

        hot_val = topic["hot_value"]
        hot_str = f"{hot_val / 1000000:.1f}M" if hot_val >= 1000000 else f"{hot_val / 1000:.1f}K" if hot_val >= 1000 else str(hot_val)
        print(f"选定话题: {topic['title']} (热度: {hot_str})\n")

        generator = ArticleGenerator(llm=llm)
        try:
            title, content = await generator.generate(topic)
        except Exception as e:
            print(f"生成文章失败: {e}")
            return

        if not content:
            print("生成失败：未获取到有效内容")
            return

        # 保存到 test_perper
        from hot_topic_generator import save_article_to_test_perper, generate_cover_image
        article_dir = save_article_to_test_perper(title, content)

        # 生成封面图片
        print(f"\n正在生成封面图片...")
        cover_path = await generate_cover_image(topic["title"], title, output_dir=Path(article_dir))
        if cover_path:
            print(f"  封面图片: {cover_path}")

        # --generate-only: 只生成不发布
        if args.generate_only:
            print(f"\n文章已保存（未发布）: {article_dir}")
            return

        # 记录原始热点标题，用于后续去重（只有发布时才记录）
        from hot_topic_generator import save_used_topic
        save_used_topic(topic["title"], title)

        # 注入 args，复用后续 publish 逻辑
        args.title = title
        args.content = content
        args.publish = True
        args._hot_article_dir = article_dir  # 保存路径，发布成功后标记

        # 传入封面图片作为第一张配图
        if cover_path:
            args.images = [str(cover_path)]
        print(f"\n正在自动发布到头条号...\n")

    # 列出用户
    if args.list_users:
        try:
            um = get_user_manager()
            users = um.get_user_ids()
            default = um.get_default_user_id()
            print(f"共 {len(users)} 个已配置用户:")
            for uid in users:
                user = um.get_user(uid)
                marker = " (默认)" if uid == default else ""
                publishing = " [发布中]" if um.is_user_publishing(uid) else ""
                print(f"  - {uid}{marker}{publishing}  auth={user.auth_file}  profile={user.chrome_profile}  port={user.cdp_port}")
        except FileNotFoundError:
            print("未找到 users.yaml 配置文件，无法列出用户")
        return

    if args.setup:
        if args.user_id:
            um = get_user_manager()
            user = um.get_user(args.user_id)
            if not user:
                print(f"错误: 未知用户 {args.user_id}")
                sys.exit(1)
            await setup_login_cloak(
                auth_file=user.auth_file,
                user_data_dir=user.chrome_profile,
                cdp_port=user.cdp_port,
            )
        else:
            await setup_login_cloak()
    elif args.publish:
        if not args.title:
            print("错误: 请使用 --title 指定文章标题")
            sys.exit(1)

        body = args.content or ""
        if args.content_file:
            try:
                body = Path(args.content_file).read_text(encoding="utf-8")
            except FileNotFoundError:
                print(f"错误: 找不到内容文件 {args.content_file}")
                sys.exit(1)
            except PermissionError:
                print(f"错误: 无权读取文件 {args.content_file}")
                sys.exit(1)

        if not body.strip():
            print("错误: 请使用 --content 或 --content-file 指定文章正文")
            sys.exit(1)

        # 验证图片文件
        image_paths = []
        if args.images:
            for img_path in args.images:
                if not Path(img_path).exists():
                    print(f"错误: 找不到图片文件 {img_path}")
                    sys.exit(1)
                image_paths.append(img_path)
            print(f"共 {len(image_paths)} 张图片待上传: {', '.join(image_paths)}")

        if args.user_id:
            # 多用户模式，通过 ConcurrentPublisher 发布
            try:
                um = get_user_manager()
            except FileNotFoundError:
                print("错误: 未找到 users.yaml 配置文件，请先配置多用户")
                sys.exit(1)
            user = um.get_user(args.user_id)
            if not user:
                print(f"错误: 未知用户 {args.user_id}")
                sys.exit(1)

            from concurrent_publisher import ConcurrentPublisher
            publisher = ConcurrentPublisher(um)
            result = await publisher.publish_for_user(
                user_id=args.user_id,
                title=args.title,
                content=body,
                image_paths=image_paths if image_paths else None,
            )
            write_result(result)
            _mark_published(result, getattr(args, '_hot_article_dir', None))
        else:
            # 单用户向后兼容模式
            result = await publish_article(args.title, body, image_paths if image_paths else None)
            write_result(result)
            _mark_published(result, getattr(args, '_hot_article_dir', None))
    else:
        parser.print_help()
        print("\n使用示例:")
        print('  1. 首次登录: python publisher.py --setup')
        print('  2. 自动发文: python publisher.py --publish --title "标题" --content "正文"')
        print('  3. 文件发文: python publisher.py --publish --title "标题" --content-file article.txt')
        print('  4. 带图片: python publisher.py --publish --title "标题" --content-file article.txt --images cover.jpg img1.jpg')
        print()
        print('  多用户模式:')
        print('  5. 列出用户: python publisher.py --list-users')
        print('  6. 用户登录: python publisher.py --setup --user-id user1')
        print('  7. 用户发文: python publisher.py --publish --user-id user1 --title "标题" --content "正文"')
        print()
        print('  热点自动生成:')
        print('  8. 自动生成并发布: python publisher.py --generate-from-hot')
        print('  9. 指定话题索引:   python publisher.py --generate-from-hot --topic-index 3')
        print()
        print('  本地文章自动发布:')
        print('  10. 自动选取未发布文章并发布: python publisher.py --auto-publish-local')
        print('  11. 指定用户发布:             python publisher.py --auto-publish-local --user-id user1')
        print()
        print('  全新 Profile 测试（反AI检测）:')
        print('  12. 逐一发布:                 python publisher.py --fresh-profile-count 5 --user-id user2')


if __name__ == "__main__":
    asyncio.run(main())
