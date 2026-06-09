"""
Core publish service - wraps publisher.py as callable HTTP service.
Supports cookie-based auth, LLM token tracking, and full request logging.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.core.runtime import EDGE_CDP_PORT, USER_DATA_DIR
from app.cookies.normalize import normalize_cookie_list
from app.core.request_logging import get_service_logger, setup_request_logger


class LLMTokenTracker:
    """Track LLM token consumption per request."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.model_name = ""
        self.call_count = 0
        self.calls = []

    def record(self, usage: dict, model: str = "", call_info: dict | None = None):
        if isinstance(usage, dict):
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_tokens += usage.get("total_tokens", 0)
        if model:
            self.model_name = model
        self.call_count += 1
        if call_info:
            self.calls.append(call_info)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "call_count": self.call_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class PublishService:
    """Toutiao article publish service."""

    def __init__(self):
        self._service_logger = get_service_logger()
        self._url_lookup_retry_delays = (0, 3, 5)
        self._publish_failure_texts = (
            "发布失败",
            "提交失败",
            "请求失败",
            "网络错误",
            "请重试",
        )

    async def publish(
        self,
        title: str,
        content: str,
        cookie: str,
        request_id: str,
        cover_image_path: Optional[str] = None,
        cover_image_url: Optional[str] = None,
        on_live_url_ready: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        """Execute article publish."""
        logger = setup_request_logger(request_id)
        tracker = LLMTokenTracker()
        start_time = asyncio.get_event_loop().time()

        logger.info("=" * 60)
        logger.info("收到请求")
        logger.info(f"  request_id: {request_id}")
        logger.info(f"  标题: {title}")
        logger.info(f"  正文长度: {len(content)} 字符")
        logger.info(f"  封面图片: {cover_image_path or cover_image_url or '（无）'}")
        logger.info(f"  Cookie长度: {len(cookie)} 字符")
        logger.info("=" * 60)

        operation_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = {
            "success": False,
            "article_url": "",
            "account": "",
            "user_name": "",
            "user_id": "",
            "failure_reason": "",
            "operation_time": operation_time,
            "article_title": title,
            "llm_usage": {},
        }

        auth_file = None
        temp_dir = None
        try:
            auth_file, temp_dir = await self._prepare_auth_from_cookie(cookie, request_id)
            logger.info("[Step 1] Cookie 解析成功并写入临时 auth 文件")
        except Exception as exc:
            fail_msg = f"Cookie 解析失败: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 1] {fail_msg}", exc_info=True)
            return self._finalize(result, tracker, start_time, logger)

        try:
            cookies = self._parse_cookie_string(cookie)
            if not cookies:
                raise ValueError("Cookie 解析结果为空")
            from app.platforms.toutiao import ToutiaoPlatform

            platform = ToutiaoPlatform()
            if not platform.validate_cookies(cookies):
                raise ValueError(f"Cookie 中未检测到 {platform.name} 的有效登录凭据")
            logger.info(f"[Step 2] Cookie 验证通过（{platform.name}），共 {len(cookies)} 项")
        except Exception as exc:
            fail_msg = f"Cookie 无效: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 2] {fail_msg}")
            return self._finalize(result, tracker, start_time, logger)

        try:
            account_info = self._extract_account_from_cookies(cookies)
            result["user_id"] = account_info.get("user_id", "")
            result["account"] = account_info.get("account_name", "")
            result["user_name"] = account_info.get("account_name", "")
            logger.info(
                f"[Step 3] 账号信息: user_id={result.get('user_id', '')}, "
                f"user_name={result.get('user_name', '')}"
            )
        except Exception as exc:
            logger.warning(f"[Step 3] 账号信息提取失败: {exc}")

        cover_path = None
        temp_cover_dir = None
        # 优先使用本地路径
        if cover_image_path:
            cover_file = Path(cover_image_path)
            if cover_file.exists():
                cover_path = str(cover_file.absolute())
                logger.info(f"[Step 4] 封面图片已确认（本地）: {cover_path}")
            else:
                logger.warning(f"[Step 4] 封面图片不存在: {cover_image_path}")
        # 如果没有本地路径但有 URL，下载到容器临时目录
        elif cover_image_url:
            try:
                temp_cover_dir = tempfile.mkdtemp(prefix=f"cover_{request_id[:8]}_")
                import requests
                response = requests.get(cover_image_url, timeout=15)
                response.raise_for_status()
                # 从 URL 提取文件名
                filename = cover_image_url.split('/')[-1].split('?')[0]
                if not filename or '.' not in filename:
                    # 从 Content-Type 推断扩展名
                    ct = response.headers.get('content-type', '')
                    ext_map = {'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
                    filename = 'cover' + ext_map.get(ct, '.jpg')
                cover_file = Path(temp_cover_dir) / filename
                cover_file.write_bytes(response.content)
                cover_path = str(cover_file.absolute())
                logger.info(f"[Step 4] 封面图片已从 URL 下载: {cover_image_url} → {cover_path}")
            except Exception as exc:
                logger.warning(f"[Step 4] 封面 URL 下载失败: {exc}，将使用头条默认封面逻辑")
                if temp_cover_dir:
                    shutil.rmtree(temp_cover_dir, ignore_errors=True)
                    temp_cover_dir = None
                cover_path = None

        try:
            llm = self._get_browser_llm()
            tracker.model_name = getattr(llm, "model", "unknown")
            logger.info(f"[Step 5] LLM 已就绪: {tracker.model_name}")
        except Exception as exc:
            fail_msg = f"LLM 初始化失败: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 5] {fail_msg}", exc_info=True)
            return self._finalize(result, tracker, start_time, logger)

        session = None
        playwright = None
        temp_profile = None
        viewer_runner = None
        viewer_playwright = None
        viewer_browser = None
        tunnel_proc = None
        try:
            logger.info("[Step 6] 正在启动浏览器...")
            browser_start = asyncio.get_event_loop().time()
            playwright, _, temp_profile = await self._launch_browser(
                user_data_dir=USER_DATA_DIR,
                cdp_port=EDGE_CDP_PORT,
                auth_file=auth_file,
                logger=logger,
            )
            from browser_use import BrowserSession
            from app.utils.browser import get_cdp_url

            session = BrowserSession(cdp_url=get_cdp_url(EDGE_CDP_PORT))
            await session.connect()
            if on_live_url_ready:
                try:
                    from app.remote.login import _find_free_port, _start_cloudflared_tunnel, _stop_process
                    from app.remote.viewer import run_viewer_server

                    viewer_port = _find_free_port()
                    viewer_runner, viewer_playwright, viewer_browser, _viewer_cdp, _viewer_page = await run_viewer_server(
                        EDGE_CDP_PORT,
                        viewer_port,
                    )
                    tunnel_proc, live_url = await _start_cloudflared_tunnel(viewer_port)
                    await on_live_url_ready(live_url)
                    logger.info("[LiveViewer] 发布实时查看链接已启动: %s", live_url)
                except Exception as exc:
                    logger.warning("[LiveViewer] 发布实时查看链接启动失败: %s", exc, exc_info=True)
            browser_elapsed = asyncio.get_event_loop().time() - browser_start
            logger.info(f"[Step 6] 浏览器启动完成，耗时 {browser_elapsed:.2f}s")

            logger.info("[Step 7] 正在构建 Agent 任务...")
            agent_start = asyncio.get_event_loop().time()
            publish_result = await self._run_agent(
                title=title,
                content=content,
                cover_path=cover_path,
                llm=llm,
                session=session,
                logger=logger,
                tracker=tracker,
            )
            agent_elapsed = asyncio.get_event_loop().time() - agent_start
            logger.info(f"[Step 7] Agent 执行完成，耗时 {agent_elapsed:.2f}s")
            result.update(publish_result)
        except Exception as exc:
            fail_msg = f"浏览器/Agent 执行异常: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 6/7] {fail_msg}", exc_info=True)
        finally:
            if session:
                await session.close()
                logger.info("[Cleanup] BrowserSession 已关闭")
            if viewer_runner:
                try:
                    await viewer_runner.cleanup()
                    logger.info("[Cleanup] Live viewer 已关闭")
                except Exception:
                    pass
            if viewer_browser:
                try:
                    await viewer_browser.close()
                except Exception:
                    pass
            if viewer_playwright:
                try:
                    await viewer_playwright.stop()
                except Exception:
                    pass
            if tunnel_proc and tunnel_proc.poll() is None:
                try:
                    _stop_process(tunnel_proc)
                    logger.info("[Cleanup] Live tunnel 已关闭")
                except Exception:
                    pass
            if playwright:
                await playwright.stop()
                logger.info("[Cleanup] Playwright 已停止")
            if temp_profile:
                shutil.rmtree(temp_profile, ignore_errors=True)
                logger.info(f"[Cleanup] 临时 profile 已清理: {temp_profile}")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"[Cleanup] 临时 auth 目录已清理: {temp_dir}")
            if temp_cover_dir:
                shutil.rmtree(temp_cover_dir, ignore_errors=True)
                logger.info(f"[Cleanup] 封面临时目录已清理: {temp_cover_dir}")

        return self._finalize(result, tracker, start_time, logger)

    async def _prepare_auth_from_cookie(self, cookie: str, request_id: str):
        temp_dir = tempfile.mkdtemp(prefix=f"publish_auth_{request_id[:8]}_")
        auth_file = Path(temp_dir) / "auth.json"
        cookies = self._parse_cookie_string(cookie)
        auth_data = {"cookies": cookies, "origins": []}
        with open(auth_file, "w", encoding="utf-8") as file_obj:
            json.dump(auth_data, file_obj, ensure_ascii=False, indent=2)
        return auth_file, temp_dir

    def _parse_cookie_string(self, cookie: str) -> list:
        try:
            parsed = json.loads(cookie)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        raw_cookies = self._extract_cookies_from_json(parsed) if parsed is not None else None
        if raw_cookies is not None:
            return normalize_cookie_list(raw_cookies)

        cookies = []
        if ";" in cookie:
            for pair in cookie.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, _, value = pair.partition("=")
                    cookies.append(
                        {
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": ".toutiao.com",
                            "path": "/",
                        }
                    )
        elif "=" in cookie:
            name, _, value = cookie.partition("=")
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".toutiao.com",
                    "path": "/",
                }
            )
        if cookies:
            return normalize_cookie_list(cookies)
        return []

    @staticmethod
    def _extract_cookies_from_json(parsed) -> list | None:
        if isinstance(parsed, list):
            return [cookie for cookie in parsed if isinstance(cookie, dict)]

        if not isinstance(parsed, dict):
            return None

        if parsed and all(isinstance(value, str) for value in parsed.values()):
            return [
                {
                    "name": key,
                    "value": value,
                    "domain": ".toutiao.com",
                    "path": "/",
                }
                for key, value in parsed.items()
            ]

        for key in ("cookies", "cookie", "results", "items", "data", "list"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [cookie for cookie in value if isinstance(cookie, dict)]
            if isinstance(value, dict):
                nested = value.get("cookies") or value.get("cookie")
                if isinstance(nested, list):
                    return [cookie for cookie in nested if isinstance(cookie, dict)]

        return None

    def _extract_account_from_cookies(self, cookies: list) -> dict:
        info = {"user_id": "", "account_name": ""}
        acct_keys = ["uid_tt", "uid_tt_ss", "tt_user_id", "user_id", "sid_tt"]
        name_keys = ["nickname", "user_name", "name"]
        for cookie in cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            if name in acct_keys and value:
                info["user_id"] = value
            if name in name_keys and value:
                info["account_name"] = value
        return info

    def _get_browser_llm(self):
        import os

        from app.publishing.deepseek import DeepSeekChatOpenAILike

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        return DeepSeekChatOpenAILike(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key=api_key,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )

    async def _launch_browser(self, user_data_dir, cdp_port, auth_file, logger):
        from app.utils.browser import get_browser_path
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        temp_dir = tempfile.mkdtemp(prefix="browser_service_")
        logger.info(f"[Browser] 使用临时 profile: {temp_dir}")
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=temp_dir,
            headless=False,
            executable_path=get_browser_path(),
            viewport={"width": 1440, "height": 1000},
            args=[
                f"--remote-debugging-port={cdp_port}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        try:
            await context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://mp.toutiao.com",
            )
            logger.info("[Browser] 已授予头条域名的剪贴板权限")
        except Exception as exc:
            logger.warning(f"[Browser] 授予剪贴板权限失败（可忽略）: {exc}")

        if auth_file and Path(auth_file).exists():
            with open(auth_file, "r", encoding="utf-8") as file_obj:
                auth_data = json.load(file_obj)
            raw_cookies = auth_data.get("cookies", [])
            if raw_cookies:
                cookies = normalize_cookie_list(raw_cookies)
                if len(cookies) != len(raw_cookies):
                    logger.warning(
                        f"[Browser] Cookie 已规范化: 原始 {len(raw_cookies)} 条 -> 有效 {len(cookies)} 条"
                    )
                await context.add_cookies(cookies)
                logger.info(f"[Browser] 注入 {len(cookies)} 条 cookie")
        return playwright, context, temp_dir

    async def _run_agent(self, title, content, cover_path, llm, session, logger, tracker):
        from browser_use import Agent

        original_ainvoke = llm.ainvoke

        async def tracked_ainvoke(self_, messages, output_format=None, **kwargs):
            input_text = ""
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict):
                        input_text += f"\n[{msg.get('role', 'unknown')}]: {msg.get('content', '')[:500]}"
                    else:
                        input_text += f"\n[{type(msg).__name__}]: {str(msg)[:500]}"

            response = await original_ainvoke(messages, output_format=output_format, **kwargs)

            output_text = ""
            if hasattr(response, "content"):
                output_text = str(response.content)[:1000]
            elif hasattr(response, "text"):
                output_text = str(response.text)[:1000]
            else:
                output_text = str(response)[:1000]

            try:
                usage = getattr(response, "usage", None)
                if usage:
                    usage_dict = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(usage, "completion_tokens", 0),
                        "total_tokens": getattr(usage, "total_tokens", 0),
                    }
                    tracker.record(
                        usage_dict,
                        tracker.model_name,
                        {
                            "input_preview": input_text[:500] if input_text else "(empty)",
                            "output_preview": output_text[:500] if output_text else "(empty)",
                            "tokens": usage_dict,
                        },
                    )
                    logger.info("-" * 60)
                    logger.info(f"LLM Call #{tracker.call_count}")
                    logger.info(f"  模型: {tracker.model_name}")
                    logger.info(f"  输入长度: {len(input_text)} 字符")
                    logger.info(
                        f"  输入预览: {input_text[:200]}..."
                        if len(input_text) > 200
                        else f"  输入: {input_text}"
                    )
                    logger.info(
                        f"  输出预览: {output_text[:200]}..."
                        if len(output_text) > 200
                        else f"  输出: {output_text}"
                    )
                    logger.info(
                        "  Token: 输入 {prompt_tokens:,} | 输出 {completion_tokens:,} | 合计 {total_tokens:,}".format(
                            **usage_dict
                        )
                    )
                    logger.info("-" * 60)
            except Exception as exc:
                logger.warning(f"[LLM Token] 无法提取 token 信息: {exc}")
            return response

        llm.ainvoke = tracked_ainvoke.__get__(llm, type(llm))

        task = self._build_publish_task(
            title=title,
            content=content,
            cover_path=cover_path,
            logger=logger,
            cover_loop_exceeded=False,
        )

        available_files = [cover_path] if cover_path else None

        controller = self._build_publish_tools(logger)
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            controller=controller,
            use_vision=False,
            max_actions_per_step=3,
            max_failures=5,
            step_timeout=120,
            available_file_paths=available_files,
        )

        logger.info(f"开始发布文章: {title}")
        publish_guard = {
            "confirm_publish_clicked": False,
            "failure_detected": None,
            "cover_action_count": 0,
            "cover_loop_exceeded": False,
        }

        async def on_step_end(agent_instance):
            try:
                history_items = getattr(agent_instance.history, "history", []) or []
                if history_items:
                    last_item = history_items[-1]
                    if not publish_guard["cover_loop_exceeded"] and self._looks_like_cover_action(last_item):
                        publish_guard["cover_action_count"] += 1
                        if publish_guard["cover_action_count"] > 3:
                            publish_guard["cover_loop_exceeded"] = True
                            logger.warning("[PublishGuard] cover_loop_limit_exceeded_skip_cover")
                    if self._did_execute_confirm_publish(last_item) and not publish_guard["confirm_publish_clicked"]:
                        publish_guard["confirm_publish_clicked"] = True
                        logger.info("[PublishGuard] confirm_publish_clicked")

                if publish_guard["confirm_publish_clicked"]:
                    failure = await self._detect_publish_failure(session, logger)
                    if failure.get("failed"):
                        publish_guard["failure_detected"] = failure
                        logger.warning(
                            "[PublishGuard] explicit_failure_signal_detected: "
                            f"{failure.get('signal', 'unknown')} | "
                            f"text={failure.get('matched_text', '')} | url={failure.get('page_url', '')}"
                        )
                        agent_instance.state.stopped = True
            except Exception as exc:
                logger.warning(f"[PublishGuard] step_end detect failed: {exc}")

        history = await agent.run(max_steps=80, on_step_end=on_step_end)
        return self._parse_agent_outcome(
            history,
            tracker=tracker,
            logger=logger,
            detected_failure=publish_guard["failure_detected"],
        )

    def _build_publish_tools(self, logger):
        from browser_use import ActionResult, Controller

        controller = Controller()

        @controller.action(
            "发布后调用此工具获取文章链接。输入本次发布标题 title。工具会打开作品管理页，最多查询 3 次同标题文章；"
            "found=true 时必须用 article_url 调用 done(success=true)；found=false 时必须用 reason 调用 done(success=false)。"
        )
        async def get_published_article_url(title: str, browser_session):
            result = await self._lookup_published_article_url(browser_session, title, logger)
            return ActionResult(
                extracted_content=json.dumps(result, ensure_ascii=False),
                include_in_memory=True,
            )

        return controller

    def _did_execute_confirm_publish(self, history_item) -> bool:
        for text in self._iter_history_result_texts(history_item):
            normalized_text = text or ""
            if not normalized_text or not self._looks_like_click_result(normalized_text):
                continue
            if "确认发布" in normalized_text or "纭鍙戝竷" in normalized_text:
                return True
        return False

    def _iter_history_result_texts(self, history_item):
        results = getattr(history_item, "result", None) or []
        for result in results:
            for field_name in ("extracted_content", "long_term_memory", "error"):
                value = getattr(result, field_name, None)
                if value:
                    yield str(value)

    @staticmethod
    def _looks_like_click_result(text: str) -> bool:
        click_markers = ("Clicked ", "clicked ", "点击", "鐐瑰嚮")
        return any(marker in text for marker in click_markers)

    def _build_publish_task(self, title, content, cover_path, logger, cover_loop_exceeded=False):
        from app.platforms.toutiao import ToutiaoPlatform
        from app.publishing.markdown import markdown_to_html

        platform = ToutiaoPlatform()
        is_markdown = self._is_markdown_content(content)
        rich_html = None

        if is_markdown:
            if logger:
                logger.info("检测到 Markdown 格式，正在转换为 HTML...")
            try:
                rich_html = markdown_to_html(content)
                if logger:
                    logger.info(f"Markdown 已转换为 HTML，长度 {len(rich_html)} 字符")
            except Exception as exc:
                if logger:
                    logger.warning(f"Markdown 转换失败: {exc}")
                rich_html = None
        elif logger:
            logger.info("检测到普通文本格式")

        if cover_loop_exceeded:
            cover_instruction = (
                "\n7. 封面设置步骤已连续重复超过 3 次。\n"
                "   - 立即跳过封面设置，不要继续在封面相关弹窗、素材库、上传对话框里循环。\n"
                "   - 关闭封面相关弹窗后，继续后续发布流程。\n"
            )
        elif cover_path:
            cover_instruction = (
                "\n7. 设置封面图片，严格按顺序执行：\n"
                "   - 先明确切换或选择为单封面模式。\n"
                "   - 如果当前是三封面、五封面或其它多封面模式，必须切回单封面。\n"
                "   - 不要选择三封面或五封面。\n"
                "   - 在封面设置区域找到并点击加号图标。\n"
                "   - 在弹出的对话框中选择本地上传。\n"
                "   - 使用 file input 上传下面这个本地文件：\n"
                f"     {cover_path}\n"
                "   - 等待上传完成，并确认页面上显示的是单封面预览。\n"
            )
        else:
            cover_instruction = (
                "\n7. 封面设置：不需要手动设置封面。\n"
                "   - 如果头条自动将正文第一张图片设为封面，保留即可。\n"
                "   - 如果未自动匹配，选择'无封面'。\n"
                "   - 不要主动去打开封面选择器或上传对话框。\n"
            )

        task = platform.get_agent_prompt(
            title=title,
            content=content,
            cover_instruction=cover_instruction,
            body_image_instruction="",
            is_markdown=is_markdown,
            rich_html=rich_html,
        )

        if logger:
            logger.info(f"任务总长度: {len(task)} 字符")
            if rich_html and rich_html in task:
                logger.info(f"任务中已嵌入完整 HTML，长度 {len(rich_html)} 字符")
            elif rich_html:
                logger.warning("任务中未找到完整 HTML，富文本可能未完整下发")
            else:
                logger.info("本次没有内嵌 rich_html")

        return task

    def _looks_like_cover_action(self, history_item) -> bool:
        cover_markers = (
            "单图",
            "替换",
            "上传图片",
            "免费正版图片",
            "热点图库",
            "封面",
            "cover",
        )
        for text in self._iter_history_result_texts(history_item):
            normalized_text = text or ""
            if any(marker in normalized_text for marker in cover_markers):
                return True
        return False

    async def _detect_publish_failure(self, session, logger) -> dict:
        try:
            page = await session.get_current_page()
            page_url = ""
            page_text = ""

            if page is not None:
                try:
                    page_url = await session.get_current_page_url()
                except Exception:
                    page_url = ""

                try:
                    page_text = await page.evaluate(
                        """() => {
                            const bodyText = document.body ? (document.body.innerText || "") : "";
                            return bodyText.slice(0, 5000);
                        }"""
                    )
                except Exception:
                    page_text = ""

            return self._detect_publish_failure_from_state(page_url=page_url, page_text=page_text)
        except Exception as exc:
            if logger:
                logger.warning(f"[PublishGuard] detect_publish_failure failed: {exc}")
            return {"failed": False, "signal": "", "page_url": "", "matched_text": ""}

    async def _reload_page(self, page, logger) -> None:
        await page.evaluate("() => window.location.reload()")
        await asyncio.sleep(2)

    async def _open_latest_article_from_articles_page(self, session, title: str, logger) -> dict:
        page = await session.get_current_page()
        if page is None:
            return {"matched": False, "article_url": "", "detail_title": ""}

        articles_url = "https://mp.toutiao.com/profile_v4/graphic/articles"
        if logger:
            logger.info(f"[PublishGuard] navigating to articles page: {articles_url}")
        # 用 page.goto 强制完整加载（不用 window.location.href 避免 React Router 拦截）
        await page.goto(articles_url)
        await asyncio.sleep(3)

        # 等待 SPA 渲染完成：等至少一个 .article-card-bone 出现
        if logger:
            logger.info("[PublishGuard] waiting for articles list SPA to render...")
        for i in range(15):
            article_count = await page.evaluate("() => document.querySelectorAll('.article-card-bone').length")
            if isinstance(article_count, int) and article_count > 0:
                if logger:
                    logger.info(f"[PublishGuard] SPA rendered: found {article_count} article cards")
                break
            await asyncio.sleep(1)
        else:
            if logger:
                logger.warning("[PublishGuard] SPA did not render any .article-card-bone within 15s")

        # 刷新一次确保最新数据
        await self._reload_page(page, logger)

        # 精准定位：.article-card-bone .title-wrap .title（头条文章列表 DOM 结构）
        first_article = await page.evaluate(
            """() => {
                // 优先精准选择器
                const titleLinks = document.querySelectorAll('.article-card-bone .title-wrap .title[href]');
                if (titleLinks.length) {
                    const first = titleLinks[0];
                    return { text: (first.innerText || first.textContent || '').trim(), href: first.href || '' };
                }
                // 次选：任意 .title[href]
                const genericTitle = document.querySelector('.title[href]');
                if (genericTitle) {
                    return { text: (genericTitle.innerText || genericTitle.textContent || '').trim(), href: genericTitle.href || '' };
                }
                // 兜底：遍历所有 /item/ 链接
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                for (const anchor of anchors) {
                    const href = anchor.href || '';
                    const text = (anchor.innerText || anchor.textContent || '').trim();
                    if (href.includes('/item/') && text) {
                        return { text, href };
                    }
                }
                return { text: '', href: '' };
            }"""
        )

        if isinstance(first_article, str):
            try:
                parsed_first_article = json.loads(first_article)
            except json.JSONDecodeError:
                parsed_first_article = None
            if isinstance(parsed_first_article, dict):
                first_article = parsed_first_article

        if not isinstance(first_article, dict):
            if logger:
                logger.warning(
                    "[PublishGuard] unexpected first_article payload: "
                    f"type={type(first_article).__name__} value={first_article!r}"
                )
            return {"matched": False, "article_url": "", "detail_title": ""}

        selected_href = str(first_article.get("href", "") or "").strip()
        selected_text = str(first_article.get("text", "") or "").strip()
        if logger:
            logger.info(
                "[PublishGuard] selected article entry: "
                f"href={selected_href} | text={selected_text}"
            )

        if not selected_href:
            if logger:
                logger.info("[PublishGuard] no /item/ article entry found on articles list page")
            return {"matched": False, "article_url": "", "detail_title": ""}

        # 关键：文章列表页的链接带有 target="_blank"，点击会打开新标签页，
        # 当前页 URL 不会跳转。所以直接从 href 提取文章 URL，不再依赖页面跳转。
        if "/item/" in selected_href:
            normalized_expected = (title or "").strip()
            normalized_actual = selected_text
            matched = bool(
                normalized_expected and normalized_actual and (
                    normalized_expected == normalized_actual
                    or normalized_expected in normalized_actual
                    or normalized_actual in normalized_expected
                )
            )
            if logger:
                logger.info(
                    "[PublishGuard] article URL extracted from href: "
                    f"expected={normalized_expected} | actual={normalized_actual} | matched={matched} | url={selected_href}"
                )
            return {
                "matched": matched,
                "article_url": selected_href if matched else "",
                "detail_title": normalized_actual,
            }

        # 如果不是 /item/ 链接（例如 preview 页面），走原来的导航逻辑
        await asyncio.sleep(2)
        initial_detail_url = await session.get_current_page_url()
        detail_url = initial_detail_url
        for _ in range(5):
            if "/article/" in detail_url:
                break
            await asyncio.sleep(1)
            detail_url = await session.get_current_page_url()
        if logger:
            logger.info(
                "[PublishGuard] article entry redirect status: "
                f"initial_url={initial_detail_url} | final_url={detail_url}"
            )
        detail_title = await page.evaluate(
            """() => {
                const candidates = [
                    'h1',
                    '[data-testid="article-title"]',
                    '.article-title',
                    '.title',
                ];
                for (const selector of candidates) {
                    const node = document.querySelector(selector);
                    const text = (node?.innerText || node?.textContent || '').trim();
                    if (text) {
                        return text;
                    }
                }
                return document.title || '';
            }"""
        )

        if not isinstance(detail_title, str):
            if logger:
                logger.warning(
                    "[PublishGuard] unexpected detail_title payload: "
                    f"type={type(detail_title).__name__} value={detail_title!r}"
                )
            detail_title = ""

        normalized_expected = (title or "").strip()
        normalized_actual = (detail_title or "").strip()
        # 放宽匹配：精确相等或双向子串包含
        matched = bool(
            normalized_expected and normalized_actual and (
                normalized_expected == normalized_actual
                or normalized_expected in normalized_actual
                or normalized_actual in normalized_expected
            )
        )

        if logger:
            logger.info(
                "[PublishGuard] article detail verification: "
                f"expected={normalized_expected} | actual={normalized_actual} | matched={matched} | url={detail_url}"
            )

        return {
            "matched": matched,
            "article_url": detail_url if matched else "",
            "detail_title": normalized_actual,
        }

    async def _lookup_published_article_url(self, session, title: str, logger) -> dict:
        last_reason = "确认发布后连续 3 次未在作品列表找到同标题文章，无法确认发布成功"
        for attempt, delay in enumerate(self._url_lookup_retry_delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                article_detail = await self._open_latest_article_from_articles_page(session, title, logger)
            except Exception as exc:
                last_reason = f"第 {attempt} 次查询作品列表失败: {exc}"
                if logger:
                    logger.warning("[PublishUrlTool] attempt=%s failed: %s", attempt, exc)
                continue
            if article_detail.get("matched") and article_detail.get("article_url"):
                return {
                    "found": True,
                    "article_url": str(article_detail.get("article_url", "") or ""),
                    "matched_title": str(article_detail.get("detail_title", "") or ""),
                    "attempts": attempt,
                    "reason": "",
                }
            if article_detail.get("detail_title"):
                last_reason = (
                    f"第 {attempt} 次查询作品列表未匹配到同标题文章，"
                    f"最近文章标题为: {article_detail.get('detail_title', '')}"
                )
            if logger:
                logger.info("[PublishUrlTool] article url not found attempt=%s title=%s", attempt, title)

        return {
            "found": False,
            "article_url": "",
            "matched_title": "",
            "attempts": len(self._url_lookup_retry_delays),
            "reason": last_reason,
        }

    def _detect_publish_failure_from_state(self, page_url: str, page_text: str) -> dict:
        normalized_url = page_url or ""
        normalized_text = page_text or ""

        for text in self._publish_failure_texts:
            if text and text in normalized_text:
                return {
                    "failed": True,
                    "signal": "failure_text",
                    "page_url": normalized_url,
                    "matched_text": text,
                }

        return {"failed": False, "signal": "", "page_url": normalized_url, "matched_text": ""}

    def _parse_agent_outcome(
        self,
        history,
        tracker,
        logger,
        detected_failure: dict | None = None,
    ):
        result = {
            "success": False,
            "article_url": "",
            "account": "",
            "failure_reason": "",
            "publish_signal": "",
        }
        try:
            final = history.final_result()
            if final:
                if logger:
                    logger.info(f"最终结果: {str(final)[:500]}")
                try:
                    parsed = json.loads(final) if isinstance(final, str) else final
                    if isinstance(parsed, dict):
                        result["success"] = bool(parsed.get("success")) or result["success"]
                        result["account"] = parsed.get("account_name", "") or parsed.get("account", "")
                        result["article_url"] = parsed.get("article_url", "") or parsed.get("url", "") or result["article_url"]
                        if not result["success"]:
                            result["failure_reason"] = parsed.get("failure_reason", "") or parsed.get("message", "")
                except (json.JSONDecodeError, TypeError):
                    if logger:
                        logger.warning("无法将最终结果解析为 JSON")
                    if history.is_successful():
                        result["success"] = True

            if history.is_successful():
                if logger:
                    logger.info(f"发布流程完成，共执行 {len(history.history)} 步")
                if not result["failure_reason"] or result["success"]:
                    result["success"] = True
                    result["failure_reason"] = ""
            else:
                if logger:
                    logger.warning("发布流程可能未成功")
                errors = history.errors() or []
                error_messages = [str(item) for item in errors if item]
                if error_messages and not result["failure_reason"] and not result["success"]:
                    result["failure_reason"] = "; ".join(error_messages)
        except AttributeError:
            if logger:
                logger.warning("无法解析 Agent 执行状态")
            if not result["success"]:
                result["failure_reason"] = "无法解析 Agent 执行状态"

        if detected_failure and detected_failure.get("failed"):
            result["success"] = False
            result["article_url"] = ""
            result["publish_signal"] = detected_failure.get("signal", "") or "guard_failure"
            result["failure_reason"] = detected_failure.get("matched_text", "") or detected_failure.get("signal", "")

        return result

    def _is_markdown_content(self, content: str) -> bool:
        indicators = [
            bool(re.match(r"^#{1,6}\s+", content, re.MULTILINE)),
            bool(re.search(r"\*\*[^*]+\*\*", content)),
            bool(re.search(r"!\[[^\]]*\]\([^)]+\)", content)),
            bool(re.search(r"\[[^\]]+\]\([^)]+\)", content)),
            bool(re.match(r"^[-*+]\s+", content, re.MULTILINE)),
            bool(re.match(r"^>\s+", content, re.MULTILINE)),
            bool(re.search(r"^---$", content, re.MULTILINE)),
            bool(re.search(r"`[^`]+`", content)),
        ]
        return any(indicators)

    def _finalize(self, result: dict, tracker: LLMTokenTracker, start_time, logger) -> dict:
        elapsed = asyncio.get_event_loop().time() - start_time
        result["elapsed_seconds"] = round(elapsed, 2)
        result["llm_usage"] = tracker.to_dict()

        receipt = {
            "success": result.get("success"),
            "article_url": result.get("article_url", ""),
            "account_name": result.get("account", ""),
            "user_id": result.get("user_id", ""),
            "article_title": result.get("article_title", ""),
            "failure_reason": result.get("failure_reason", ""),
            "elapsed_seconds": result["elapsed_seconds"],
            "llm_usage": result["llm_usage"],
            "llm_call_count": tracker.call_count,
            "llm_calls": tracker.calls,
            "operation_time": result.get("operation_time", ""),
        }
        result["receipt"] = receipt

        logger.info("=" * 60)
        logger.info("发布结果")
        logger.info(f"  状态: {'成功' if result.get('success') else '失败'}")
        logger.info(f"  账号: {result.get('account', '')}")
        logger.info(f"  用户ID: {result.get('user_id', '')}")
        logger.info(f"  文章: {result.get('article_title', '')}")
        logger.info(f"  URL: {result.get('article_url') or '（无）'}")
        if result.get("failure_reason"):
            logger.info(f"  失败原因: {result['failure_reason']}")
        logger.info("耗时统计")
        logger.info(f"  总耗时: {elapsed:.2f}s")
        logger.info(f"  LLM 调用: {tracker.call_count} 次")
        logger.info(f"  模型: {tracker.model_name}")
        logger.info(f"  Token 输入: {tracker.prompt_tokens:,}")
        logger.info(f"  Token 输出: {tracker.completion_tokens:,}")
        logger.info(f"  Token 合计: {tracker.total_tokens:,}")
        logger.info("=" * 60)

        return result
