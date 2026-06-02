"""
Core publish service - wraps publisher.py as callable HTTP service.
Supports cookie-based auth, LLM token tracking, and full request logging.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from publish_service.config import USER_DATA_DIR, EDGE_CDP_PORT
from publish_service.logger_config import setup_request_logger, get_service_logger


class LLMTokenTracker:
    """Track LLM token consumption per request."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.model_name = ""
        self.call_count = 0
        self.calls = []

    def record(self, usage: dict, model: str = "", call_info: dict = None):
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

    async def publish(
        self,
        title: str,
        content: str,
        cookie: str,
        request_id: str,
        cover_image_path: Optional[str] = None,
    ) -> dict:
        """Execute article publish."""
        logger = setup_request_logger(request_id)
        tracker = LLMTokenTracker()
        start_time = asyncio.get_event_loop().time()

        logger.info("=" * 60)
        logger.info("【收到请求】")
        logger.info(f"  request_id: {request_id}")
        logger.info(f"  标题: {title}")
        logger.info(f"  正文长度: {len(content)} 字符")
        logger.info(f"  封面图片: {cover_image_path or '（无）'}")
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
            logger.info("[Step 1] ✅ Cookie 解析成功并写入临时 auth 文件")
        except Exception as exc:
            fail_msg = f"Cookie 解析失败: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 1] ❌ {fail_msg}", exc_info=True)
            return self._finalize(result, tracker, start_time, logger)

        try:
            cookies = self._parse_cookie_string(cookie)
            if not cookies:
                raise ValueError("Cookie 解析结果为空")
            from platforms.toutiao import ToutiaoPlatform

            platform = ToutiaoPlatform()
            if not platform.validate_cookies(cookies):
                raise ValueError(f"Cookie 中未检测到 {platform.name} 的有效登录凭证")
            logger.info(f"[Step 2] ✅ Cookie 验证通过（{platform.name}），共 {len(cookies)} 项")
        except Exception as exc:
            fail_msg = f"Cookie 无效: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 2] ❌ {fail_msg}")
            return self._finalize(result, tracker, start_time, logger)

        try:
            account_info = self._extract_account_from_cookies(cookies)
            result["user_id"] = account_info.get("user_id", "")
            result["account"] = account_info.get("account_name", "")
            result["user_name"] = account_info.get("account_name", "")
            logger.info(
                f"[Step 3] ✅ 账号: user_id={result.get('user_id', '')}, "
                f"user_name={result.get('user_name', '')}"
            )
        except Exception as exc:
            logger.warning(f"[Step 3] ⚠️ 账号信息提取失败: {exc}")

        cover_path = None
        if cover_image_path:
            cover_file = Path(cover_image_path)
            if cover_file.exists():
                cover_path = str(cover_file.absolute())
                logger.info(f"[Step 4] ✅ 封面图片已确认: {cover_path}")
            else:
                logger.warning(f"[Step 4] ⚠️ 封面图片不存在: {cover_image_path}")

        try:
            llm = self._get_browser_llm()
            tracker.model_name = getattr(llm, "model", "unknown")
            logger.info(f"[Step 5] ✅ LLM 已就绪: {tracker.model_name}")
        except Exception as exc:
            fail_msg = f"LLM 初始化失败: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 5] ❌ {fail_msg}", exc_info=True)
            return self._finalize(result, tracker, start_time, logger)

        session = None
        playwright = None
        temp_profile = None
        try:
            logger.info("[Step 6] 正在启动浏览器...")
            browser_start = asyncio.get_event_loop().time()
            playwright, _, temp_profile = await self._launch_browser(
                user_data_dir=USER_DATA_DIR,
                cdp_port=EDGE_CDP_PORT,
                auth_file=auth_file,
                logger=logger,
            )
            from browser_utils import get_cdp_url
            from browser_use import BrowserSession

            session = BrowserSession(cdp_url=get_cdp_url(EDGE_CDP_PORT))
            await session.connect()
            browser_elapsed = asyncio.get_event_loop().time() - browser_start
            logger.info(f"[Step 6] ✅ 浏览器启动完成，耗时 {browser_elapsed:.2f}s")

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
            logger.info(f"[Step 7] ✅ Agent 执行完成，耗时 {agent_elapsed:.2f}s")
            result.update(publish_result)
        except Exception as exc:
            fail_msg = f"浏览器/Agent 执行异常: {exc}"
            result["failure_reason"] = fail_msg
            logger.error(f"[Step 6/7] ❌ {fail_msg}", exc_info=True)
        finally:
            if session:
                await session.close()
                logger.info("【Cleanup】BrowserSession 已关闭")
            if playwright:
                await playwright.stop()
                logger.info("【Cleanup】Playwright 已停止")
            if temp_profile:
                shutil.rmtree(temp_profile, ignore_errors=True)
                logger.info(f"【Cleanup】临时 profile 已清理: {temp_profile}")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"【Cleanup】临时 auth 目录已清理: {temp_dir}")

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
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "cookies" in parsed:
                return parsed["cookies"]
        except (json.JSONDecodeError, TypeError):
            pass

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
        return cookies

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
        from browser_use.llm.openai.like import ChatOpenAILike

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        return ChatOpenAILike(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key=api_key,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )

    async def _launch_browser(self, user_data_dir, cdp_port, auth_file, logger):
        from playwright.async_api import async_playwright
        from browser_utils import get_browser_path

        playwright = await async_playwright().start()
        temp_dir = tempfile.mkdtemp(prefix="browser_service_")
        logger.info(f"【Browser】使用临时 profile: {temp_dir}")
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=temp_dir,
            headless=False,
            executable_path=get_browser_path(),
            viewport={"width": 1440, "height": 1000},
            args=[f"--remote-debugging-port={cdp_port}"],
        )
        try:
            await context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://mp.toutiao.com",
            )
            logger.info("【Browser】已授予头条域名的剪贴板权限")
        except Exception as exc:
            logger.warning(f"【Browser】授予剪贴板权限失败（可忽略）: {exc}")

        if auth_file and Path(auth_file).exists():
            with open(auth_file, "r", encoding="utf-8") as file_obj:
                auth_data = json.load(file_obj)
            cookies = auth_data.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
                logger.info(f"【Browser】注入 {len(cookies)} 项 cookie")
        return playwright, context, temp_dir

    async def _run_agent(self, title, content, cover_path, llm, session, logger, tracker):
        from browser_use import Agent
        from platforms.toutiao import ToutiaoPlatform

        platform = ToutiaoPlatform()
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
                    logger.info(f"【LLM Call #{tracker.call_count}】")
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

        is_markdown = self._is_markdown_content(content)
        rich_html = None
        if is_markdown:
            logger.info("【Agent】检测到 Markdown 格式，正在转换为 HTML...")
            try:
                from publish_service.markdown_to_rich import markdown_to_html

                rich_html = markdown_to_html(content)
                logger.info(f"【Agent】Markdown 已转换为 HTML（{len(rich_html)} 字符）")
            except Exception as exc:
                logger.warning(f"【Agent】Markdown 转换失败: {exc}")
                rich_html = None
        else:
            logger.info("【Agent】检测到普通文本格式")

        if cover_path:
            cover_instruction = (
                "\n7. 设置封面图片（严格按顺序）：\n"
                "   - 在封面设置区域找到并点击加号图标\n"
                "   - 在弹出的对话框中选择本地上传\n"
                "   - 使用 file input 上传下面这个本地文件：\n"
                f"     {cover_path}\n"
                "   - 等待上传完成并确认封面预览已经出现\n"
            )
        else:
            cover_instruction = "\n7. 检查封面设置；如果系统自动匹配成功则保留，否则选择无封面。\n"

        task = platform.get_agent_prompt(
            title=title,
            content=content,
            cover_instruction=cover_instruction,
            body_image_instruction="",
            is_markdown=is_markdown,
            rich_html=rich_html,
        )

        logger.info(f"【Agent】task 总长度: {len(task)} 字符")
        if rich_html and rich_html in task:
            logger.info(f"【Agent】task 中已嵌入完整 HTML（{len(rich_html)} 字符）")
        elif rich_html:
            logger.warning("【Agent】task 中未找到完整 HTML，富文本可能未完整下发")
        else:
            logger.info("【Agent】本次没有内嵌 rich_html")

        available_files = [cover_path] if cover_path else None

        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            use_vision=False,
            max_actions_per_step=3,
            max_failures=5,
            step_timeout=120,
            available_file_paths=available_files,
        )

        logger.info(f"【Agent】开始发布文章: {title}")
        history = await agent.run(max_steps=80)

        result = {"success": False, "article_url": "", "account": "", "failure_reason": ""}
        try:
            final = history.final_result()
            if final:
                logger.info(f"【Agent】最终结果: {str(final)[:500]}")
                try:
                    parsed = json.loads(final) if isinstance(final, str) else final
                    if isinstance(parsed, dict):
                        result["success"] = bool(parsed.get("success"))
                        result["account"] = parsed.get("account_name", "")
                        result["article_url"] = parsed.get("article_url", "")
                        result["failure_reason"] = parsed.get("failure_reason", "")
                except (json.JSONDecodeError, TypeError):
                    logger.warning("【Agent】无法将最终结果解析为 JSON")

            if history.is_successful():
                logger.info(f"【Agent】发布流程完成，共执行 {len(history.history)} 步")
                if not result["failure_reason"]:
                    result["success"] = True
            else:
                logger.warning("【Agent】发布流程可能未成功")
                errors = history.errors() or []
                error_messages = [str(item) for item in errors if item]
                if error_messages and not result["failure_reason"]:
                    result["failure_reason"] = "; ".join(error_messages)
        except AttributeError:
            logger.warning("【Agent】无法解析 Agent 执行状态")
            result["failure_reason"] = "无法解析 Agent 执行状态"

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
        return sum(1 for item in indicators if item) >= 2

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
        logger.info("【发布结果】")
        logger.info(f"  状态: {'✅ 成功' if result.get('success') else '❌ 失败'}")
        logger.info(f"  账号: {result.get('account', '')}")
        logger.info(f"  用户ID: {result.get('user_id', '')}")
        logger.info(f"  文章: {result.get('article_title', '')}")
        logger.info(f"  URL: {result.get('article_url') or '（无）'}")
        if result.get("failure_reason"):
            logger.info(f"  失败原因: {result['failure_reason']}")
        logger.info("【耗时统计】")
        logger.info(f"  总耗时: {elapsed:.2f}s")
        logger.info(f"  LLM 调用: {tracker.call_count} 次")
        logger.info(f"  模型: {tracker.model_name}")
        logger.info(f"  Token 输入: {tracker.prompt_tokens:,}")
        logger.info(f"  Token 输出: {tracker.completion_tokens:,}")
        logger.info(f"  Token 合计: {tracker.total_tokens:,}")
        logger.info("=" * 60)

        return result
