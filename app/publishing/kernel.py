"""
Core publish service - wraps publisher.py as callable HTTP service.
Supports cookie-based auth, LLM token tracking, and full request logging.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.core.runtime import USER_DATA_DIR
from app.cookies.normalize import normalize_cookie_list
from app.platforms.base import PlatformConfig
from app.publishing.agent_trace import AgentTraceState, llm_trace_line, log_step_trace
from app.core.request_logging import get_service_logger, setup_request_logger
from app.remote.login import _find_free_port
from app.utils.urls import normalize_toutiao_article_url


@dataclass
class PublishTaskBundle:
    task: str
    plain_text: str
    rich_html: str
    body_probe: str
    is_markdown: bool
    platform_name: str


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

    _browser_launch_attempts = 3

    def __init__(self):
        self._service_logger = get_service_logger()
        self._url_lookup_retry_delays = (0, 3, 5)
        # 发文渠道句柄：用于按渠道获取绑定的代理 IP。由 publish() 入参注入，
        # 默认空串（代理未启用 / 无渠道时直连）。
        self._channel_id = ""

    def _platform(self):
        from app.platforms.toutiao import ToutiaoPlatform

        return ToutiaoPlatform()

    async def publish(
        self,
        title: str,
        content: str,
        cookie: str,
        request_id: str,
        cover_image_path: Optional[str] = None,
        cover_image_url: Optional[str] = None,
        # 回调入参是 viewer 的本机端口（int），由上层据此构造对外 live_url。
        on_live_url_ready: Callable[[int], Awaitable[None]] | None = None,
        # 发文渠道句柄：用于按渠道获取绑定的代理 IP（代理未启用/空串时直连）。
        channel_id: str = "",
    ) -> dict:
        """Execute article publish."""
        self._channel_id = channel_id or ""
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

            platform = self._platform()
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
        try:
            logger.info("[Step 6] 正在启动浏览器...")
            browser_start = asyncio.get_event_loop().time()
            playwright, _, temp_profile, publish_cdp_port = await self._launch_isolated_publish_browser(
                auth_file=auth_file,
                platform_name=platform.name,
                logger=logger,
            )
            from browser_use import BrowserSession
            from app.utils.browser import get_cdp_url

            logger.info("[BrowserSession] 连接发布浏览器 CDP: 127.0.0.1:%s", publish_cdp_port)
            session = BrowserSession(cdp_url=get_cdp_url(publish_cdp_port))
            await session.connect()
            if on_live_url_ready:
                try:
                    from app.remote.viewer import run_viewer_server

                    viewer_port = _find_free_port()
                    logger.info(
                        "[LiveViewer] 绑定发布浏览器 CDP: %s viewer_port=%s",
                        publish_cdp_port,
                        viewer_port,
                    )
                    viewer_runner, viewer_playwright, viewer_browser, _viewer_cdp, _viewer_page = await run_viewer_server(
                        publish_cdp_port,
                        viewer_port,
                    )
                    # viewer 绑定本机随机端口，把端口回调给上层（orchestrator）：由它登记
                    # job_id→port 并构造经主服务反代的对外 live_url（见 publish_viewer_proxy）。
                    logger.info("[LiveViewer] 发布实时查看已启动: 127.0.0.1:%s", viewer_port)
                    await on_live_url_ready(viewer_port)
                except Exception as exc:
                    logger.warning("[LiveViewer] 发布实时查看启动失败: %s", exc, exc_info=True)
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
                await self._cleanup_awaitable_with_timeout(
                    label="LiveViewer runner",
                    awaitable_factory=viewer_runner.cleanup,
                    timeout_seconds=3.0,
                    logger=logger,
                )
            if viewer_browser:
                await self._cleanup_awaitable_with_timeout(
                    label="LiveViewer browser",
                    awaitable_factory=viewer_browser.close,
                    timeout_seconds=2.0,
                    logger=logger,
                )
            if viewer_playwright:
                await self._cleanup_awaitable_with_timeout(
                    label="LiveViewer playwright",
                    awaitable_factory=viewer_playwright.stop,
                    timeout_seconds=2.0,
                    logger=logger,
                )
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
        from app.llm.openai_compat import OpenAICompatibleChat

        api_key = os.getenv("LLM_API_KEY", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "http://47.242.205.13:8110/v1").strip()
        model = os.getenv("LLM_MODEL", "Qwen/Qwen3.5-397B-A17B").strip()

        if not api_key:
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            if api_key:
                base_url = "https://api.deepseek.com/v1"
                model = "deepseek-chat"

        if not api_key:
            raise RuntimeError("LLM_API_KEY is not set")

        return OpenAICompatibleChat(
            model=model,
            base_url=base_url,
            api_key=api_key,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )

    @staticmethod
    def _get_browser_vision_config() -> dict:
        raw_use_vision = os.getenv("BROWSER_USE_VISION", "true").strip().lower()
        raw_detail = os.getenv("BROWSER_USE_VISION_DETAIL", "low").strip().lower()

        if raw_use_vision in ("1", "true", "yes", "on"):
            use_vision = True
        elif raw_use_vision in ("0", "false", "no", "off"):
            use_vision = False
        elif raw_use_vision == "auto":
            use_vision = "auto"
        else:
            use_vision = True

        if raw_detail not in ("auto", "low", "high"):
            raw_detail = "low"

        return {
            "use_vision": use_vision,
            "vision_detail_level": raw_detail,
        }

    async def _launch_isolated_publish_browser(self, auth_file, logger, platform_name: str = ""):
        last_exc = None
        for attempt in range(1, self._browser_launch_attempts + 1):
            publish_cdp_port = _find_free_port()
            logger.info(
                "[Browser] 分配发布浏览器 CDP 端口 attempt=%s/%s cdp_port=%s",
                attempt,
                self._browser_launch_attempts,
                publish_cdp_port,
            )
            try:
                playwright, context, temp_profile = await self._launch_browser(
                    user_data_dir=USER_DATA_DIR,
                    cdp_port=publish_cdp_port,
                    auth_file=auth_file,
                    platform_name=platform_name,
                    logger=logger,
                )
                return playwright, context, temp_profile, publish_cdp_port
            except Exception as exc:
                last_exc = exc
                if not self._looks_like_port_conflict(exc) or attempt >= self._browser_launch_attempts:
                    raise
                logger.warning(
                    "[Browser] 发布浏览器 CDP 端口可能被占用，准备重试 attempt=%s/%s cdp_port=%s error=%s",
                    attempt,
                    self._browser_launch_attempts,
                    publish_cdp_port,
                    exc,
                )
        raise last_exc or RuntimeError("发布浏览器启动失败")

    @staticmethod
    def _looks_like_port_conflict(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "address already in use",
            "eaddrinuse",
            "bind",
            "port",
            "cannot start http server",
            "remote-debugging-port",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _clipboard_permission_origins(platform_name: str) -> list[str]:
        origins = {
            "sohu": ["https://mp.sohu.com"],
            "toutiao": ["https://mp.toutiao.com"],
        }
        return origins.get((platform_name or "").strip().lower(), ["https://mp.toutiao.com"])

    async def _launch_browser(self, user_data_dir, cdp_port, auth_file, logger, platform_name: str = ""):
        from app.utils.browser import get_browser_path
        from playwright.async_api import async_playwright

        # 按渠道获取绑定的代理（代理未启用/无渠道时返回 (None, None)，直连）。
        # 严格模式：代理获取失败会向上抛出，不 fallback 直连，保护真实 IP。
        from app.proxy.browser import get_channel_proxy

        proxy_dict, proxy_info = await get_channel_proxy(self._channel_id)
        if proxy_dict:
            logger.info(
                "[Proxy] 发布浏览器走代理: server=%s auth=%s",
                proxy_dict.get("server"),
                "是" if proxy_dict.get("username") else "否",
            )

        playwright = await async_playwright().start()
        temp_dir = tempfile.mkdtemp(prefix="browser_service_")
        logger.info(f"[Browser] 使用临时 profile: {temp_dir}")
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=temp_dir,
            headless=False,
            executable_path=get_browser_path(),
            viewport={"width": 1440, "height": 1000},
            proxy=proxy_dict,  # None 时 Playwright 忽略，等价直连
            args=[
                f"--remote-debugging-port={cdp_port}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        # 出口 IP 验证：确认浏览器实际出口 IP 与代理一致（受 defaults.verify_exit_ip 开关
        # 控制）。调试期失败仅 warning 不阻断，避免误杀；严格化可改为抛出。
        if proxy_info is not None:
            await self._verify_exit_ip(context, proxy_info, logger)
        for origin in self._clipboard_permission_origins(platform_name):
            try:
                await context.grant_permissions(
                    ["clipboard-read", "clipboard-write"],
                    origin=origin,
                )
                logger.info(
                    "[Browser] 已授予 %s 剪贴板权限 origin=%s",
                    platform_name or "default",
                    origin,
                )
            except Exception as exc:
                logger.warning("[Browser] 授予剪贴板权限失败 origin=%s error=%s", origin, exc)

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

    async def _verify_exit_ip(self, context, proxy_info, logger) -> None:
        """校验浏览器实际出口 IP 是否等于代理 IP。

        受代理配置 defaults.verify_exit_ip 开关控制；关闭时直接跳过。调试期验证失败
        仅记录 warning 不阻断流程（避免 ip 探测站临时不可用误杀发文）。
        """
        from app.proxy.assignment import get_assignment_manager
        from app.proxy.verifier import ExitIPVerifier

        mgr = get_assignment_manager()
        if mgr is None or not mgr.config.defaults.verify_exit_ip:
            return
        check_url = mgr.config.defaults.exit_ip_check_url
        try:
            await ExitIPVerifier().verify(context, proxy_info, check_url=check_url)
        except Exception as exc:
            logger.warning("[Proxy] 出口 IP 验证未通过（不阻断发文）: %s", exc)

    async def _run_agent(self, title, content, cover_path, llm, session, logger, tracker):
        from browser_use import Agent

        original_ainvoke = llm.ainvoke
        trace_state = AgentTraceState()
        trace_summary = {
            "repeat_warnings": [],
            "last_intent": "",
            "last_action": "",
            "llm_calls": [],
        }
        current_step_ref = {"step": None}

        async def tracked_ainvoke(self_, messages, output_format=None, **kwargs):
            input_text = ""
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict):
                        input_text += f"\n[{msg.get('role', 'unknown')}]: {msg.get('content', '')[:500]}"
                    else:
                        input_text += f"\n[{type(msg).__name__}]: {str(msg)[:500]}"

            llm_start = time.perf_counter()
            response = await original_ainvoke(messages, output_format=output_format, **kwargs)
            llm_elapsed = time.perf_counter() - llm_start

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
                    trace_summary["llm_calls"].append(llm_elapsed)
                    logger.info(
                        llm_trace_line(
                            call=tracker.call_count,
                            step=current_step_ref.get("step"),
                            duration_seconds=llm_elapsed,
                            model=tracker.model_name,
                            usage=usage_dict,
                        )
                    )
                    logger.info("-" * 60)
                    logger.info(f"LLM Call #{tracker.call_count}")
                    logger.info(f"  模型: {tracker.model_name}")
                    logger.info(f"  耗时: {llm_elapsed:.2f}s")
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

        task_bundle = self._build_publish_task(
            title=title,
            content=content,
            cover_path=cover_path,
            logger=logger,
            cover_loop_exceeded=False,
        )
        task = task_bundle.task

        available_files = [cover_path] if cover_path else None

        vision_config = self._get_browser_vision_config()
        logger.info(
            "[Vision] browser-use vision config: use_vision=%s | vision_detail_level=%s",
            vision_config["use_vision"],
            vision_config["vision_detail_level"],
        )

        controller = self._build_publish_tools(
            logger,
            original_title=title,
            body_payload=task_bundle,
        )
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            controller=controller,
            use_vision=vision_config["use_vision"],
            vision_detail_level=vision_config["vision_detail_level"],
            max_actions_per_step=3,
            max_failures=5,
            step_timeout=120,
            available_file_paths=available_files,
        )

        logger.info(f"开始发布文章: {title}")
        publish_guard = {
            "confirm_publish_clicked": False,
            "forced_result": None,
            "failure_detected": None,
            "done_action_seen_at": 0.0,
            "done_action_step": 0,
            "cover_action_count": 0,
            "cover_loop_exceeded": False,
            "body_paste_attempts": 0,
            "body_paste_succeeded": False,
            "last_url": "",
            "last_editor_text_len": 0,
            "last_editor_preview": "",
            "last_editor_source": "none",
            "last_probe_found": False,
            "step_summaries": [],
        }

        # 正文存在性探针：与平台 prompt 共用 PlatformConfig.body_probe，确保两边算出的字符串相同
        body_probe = PlatformConfig.body_probe(content)
        expected_body_text_length = len("".join((task_bundle.plain_text or "").split()))

        async def on_step_end(agent_instance):
            try:
                history_items = getattr(agent_instance.history, "history", []) or []
                if not history_items:
                    return
                last_item = history_items[-1]
                step_number = len(history_items)
                current_step_ref["step"] = step_number

                # 每步抓取页面状态用于诊断
                try:
                    page_state = await self._capture_page_state(session, body_probe)
                except Exception as exc:
                    logger.warning(f"[StepDiag {step_number}] state capture failed: {exc}")
                    page_state = {}

                trace = log_step_trace(
                    logger=logger,
                    step_number=step_number,
                    history_item=last_item,
                    page_state=page_state,
                    state=trace_state,
                )
                trace_summary["last_intent"] = trace.intent
                trace_summary["last_action"] = trace.action_signature
                trace_summary["repeat_warnings"].extend(trace.repeat_warnings)

                self._log_step_diagnostics(step_number, last_item, page_state, logger)

                # 更新 publish_guard 中最近状态（用于失败摘要）
                publish_guard["last_url"] = page_state.get("url", "") or ""
                publish_guard["last_editor_text_len"] = page_state.get("editor_text_length", 0) or 0
                publish_guard["last_editor_preview"] = (
                    page_state.get("editor_text_preview", "") or ""
                )
                publish_guard["last_editor_source"] = page_state.get("editor_source", "none")
                publish_guard["last_probe_found"] = bool(page_state.get("probe_found", False))

                # 跟踪正文粘贴尝试
                action_summary = self._extract_action_summary(last_item)
                if self._action_summary_is_done(action_summary) and not publish_guard["done_action_seen_at"]:
                    publish_guard["done_action_seen_at"] = time.perf_counter()
                    publish_guard["done_action_step"] = step_number
                    logger.info("[AgentRun] done_action_seen step=%s", step_number)
                if action_summary and self._looks_like_paste_action(action_summary):
                    publish_guard["body_paste_attempts"] += 1
                    editor_len_after = publish_guard["last_editor_text_len"]
                    probe_found = publish_guard["last_probe_found"]
                    logger.info(
                        f"[BodyWrite] paste_attempt={publish_guard['body_paste_attempts']} "
                        f"editor_len_after={editor_len_after} probe_found={probe_found} "
                        f"editor_source={publish_guard['last_editor_source']}"
                    )
                    # 必须 editor 真的有内容 + 探针字符串命中 才算成功
                    # 4738382 之前的 false positive：editor_len 来自 document.body（页面 chrome），
                    # 任何时候都 > 5，误判 paste_succeeded=True。修后必须 probe_found
                    if editor_len_after > 30 and probe_found:
                        publish_guard["body_paste_succeeded"] = True
                        logger.info(
                            f"[BodyWrite] paste_succeeded: editor_len={editor_len_after}"
                        )
                    else:
                        logger.warning(
                            f"[BodyWrite] paste_attempt_failed: "
                            f"editor_len={editor_len_after} probe_found={probe_found} "
                            f"editor_source={publish_guard['last_editor_source']}"
                        )

                if action_summary and self._looks_like_publish_action(action_summary):
                    completeness = self._body_completeness_from_state(
                        page_state=page_state,
                        expected_text_length=expected_body_text_length,
                    )
                    if not completeness["ok"]:
                        failure_reason = (
                            "正文写入不完整："
                            f"expected={completeness['expected_text_length']} "
                            f"actual={completeness['editor_text_length']} "
                            f"probe_found={completeness['probe_found']} "
                            f"source={completeness['editor_source']}"
                        )
                        publish_guard["failure_detected"] = {
                            "failed": True,
                            "signal": "body_incomplete_before_publish",
                            "page_url": page_state.get("url", ""),
                            "matched_text": failure_reason,
                        }
                        publish_guard["forced_result"] = {
                            "success": False,
                            "article_url": "",
                            "account": "",
                            "failure_reason": failure_reason,
                            "publish_signal": "body_incomplete_before_publish",
                        }
                        logger.error("[PublishGuard] %s", failure_reason)
                        self._log_final_summary(
                            publish_guard,
                            publish_guard["forced_result"],
                            logger,
                            trace_summary=trace_summary,
                        )
                        agent_instance.state.stopped = True
                        return

                # 跟踪步骤摘要（保留最近 10 步）
                step_summary = {
                    "step": step_number,
                    "url": publish_guard["last_url"],
                    "action": action_summary,
                    "result": self._extract_result_summary(last_item),
                    "editor_len": publish_guard["last_editor_text_len"],
                }
                publish_guard["step_summaries"].append(step_summary)
                if len(publish_guard["step_summaries"]) > 10:
                    publish_guard["step_summaries"] = publish_guard["step_summaries"][-10:]

                # 原有 cover loop 防护
                if not publish_guard["cover_loop_exceeded"] and self._looks_like_cover_action(last_item):
                    publish_guard["cover_action_count"] += 1
                    if publish_guard["cover_action_count"] > 3:
                        publish_guard["cover_loop_exceeded"] = True
                        logger.warning("[PublishGuard] cover_loop_limit_exceeded_skip_cover")
                # 原有确认发布检测
                if self._did_execute_confirm_publish(last_item) and not publish_guard["confirm_publish_clicked"]:
                    publish_guard["confirm_publish_clicked"] = True
                    logger.info("[PublishGuard] confirm_publish_clicked")
            except Exception as exc:
                logger.warning(f"[PublishGuard] step_end detect failed: {exc}")

        agent_run_start = time.perf_counter()
        logger.info("[AgentRun] start max_steps=80")
        history = await agent.run(max_steps=80, on_step_end=on_step_end)
        agent_run_elapsed = time.perf_counter() - agent_run_start
        if publish_guard["done_action_seen_at"]:
            done_return_elapsed = time.perf_counter() - publish_guard["done_action_seen_at"]
            logger.info(
                "[AgentRun] returned duration=%.2fs done_to_return=%.2fs done_step=%s",
                agent_run_elapsed,
                done_return_elapsed,
                publish_guard["done_action_step"],
            )
        else:
            logger.info("[AgentRun] returned duration=%.2fs done_seen=false", agent_run_elapsed)
        result = self._parse_agent_outcome(
            history,
            tracker=tracker,
            logger=logger,
            detected_failure=publish_guard["failure_detected"],
        )
        if publish_guard.get("forced_result"):
            result.update(publish_guard["forced_result"])
        self._log_final_summary(publish_guard, result, logger, trace_summary=trace_summary)
        return result

    def _build_publish_tools(
        self,
        logger,
        original_title: str = "",
        body_payload: PublishTaskBundle | None = None,
    ):
        from browser_use import ActionResult, Controller
        from app.publishing.tools import (
            PUBLISH_OBSERVATION_SCRIPT,
            PUBLISH_OBSERVER_SCRIPT,
            BodyWritePayload,
            BodyWriter,
            build_terminal_failure_payload,
            normalize_observed_snapshot,
        )

        controller = Controller()
        writer = None
        platform_name = (getattr(body_payload, "platform_name", "") or "").lower() if body_payload else ""
        if body_payload:
            writer = BodyWriter(
                BodyWritePayload(
                    plain_text=body_payload.plain_text,
                    rich_html=body_payload.rich_html,
                    body_probe=body_payload.body_probe,
                    platform_name=body_payload.platform_name,
                ),
                logger=logger,
            )

        @controller.action(
            "将本次文章正文以普通文本方式写入当前平台正文编辑器。无参数。"
            "返回 ok=true 且 probe_found=true 后才允许继续封面、预览或发布。"
        )
        async def paste_plain_text_body(browser_session):
            tool_start = time.perf_counter()
            if writer is None:
                result = {
                    "ok": False,
                    "mode": "plain_text",
                    "method": "",
                    "editor_text_length": 0,
                    "probe_found": False,
                    "reason": "body_payload_unavailable",
                }
            else:
                result = await writer.paste_plain_text_body(browser_session)
            if logger:
                logger.info(
                    "[AgentTool] name=paste_plain_text_body duration=%.2fs ok=%s "
                    "method=%s probe_found=%s editor_len=%s reason=%r",
                    time.perf_counter() - tool_start,
                    result.get("ok"),
                    result.get("method", ""),
                    result.get("probe_found"),
                    result.get("editor_text_length", 0),
                    result.get("reason", ""),
                )
            return ActionResult(
                extracted_content=json.dumps(result, ensure_ascii=False),
                include_in_memory=True,
            )

        @controller.action(
            "将系统已准备好的 Markdown 富文本 HTML 写入当前平台正文编辑器。无参数。"
            "返回 ok=true 且 probe_found=true 后才允许继续封面、预览或发布。"
        )
        async def paste_rich_html_body(browser_session):
            tool_start = time.perf_counter()
            if writer is None:
                result = {
                    "ok": False,
                    "mode": "rich_html",
                    "method": "",
                    "editor_text_length": 0,
                    "probe_found": False,
                    "reason": "body_payload_unavailable",
                }
            else:
                result = await writer.paste_rich_html_body(browser_session)
            if logger:
                logger.info(
                    "[AgentTool] name=paste_rich_html_body duration=%.2fs ok=%s "
                    "method=%s probe_found=%s editor_len=%s reason=%r",
                    time.perf_counter() - tool_start,
                    result.get("ok"),
                    result.get("method", ""),
                    result.get("probe_found"),
                    result.get("editor_text_length", 0),
                    result.get("reason", ""),
                )
            return ActionResult(
                extracted_content=json.dumps(result, ensure_ascii=False),
                include_in_memory=True,
            )

        @controller.action(
            "当你根据页面证据判断文章已经发布成功或已提交审核后，必须调用本工具获取 article_url。"
            "输入本次发布标题 title；工具会尝试查找同标题文章 URL。"
            "如果未返回有效 article_url，发文任务不能返回 success=true；请观察页面和工具证据后调用 finish_publish_failed。"
        )
        async def get_published_article_url(title: str, browser_session):
            tool_start = time.perf_counter()
            lookup_title = (original_title or title or "").strip()
            if logger and title and lookup_title != title:
                logger.info(
                    "[PublishUrlTool] ignoring llm supplied title; "
                    "llm_title=%s | lookup_title=%s",
                    title,
                    lookup_title,
                )
            result = await self._lookup_published_article_url(browser_session, lookup_title, logger)
            tool_elapsed = time.perf_counter() - tool_start
            if logger:
                logger.info(
                    "[AgentTool] name=get_published_article_url duration=%.2fs "
                    "title=%r attempts=%s found=%s article_url=%s reason=%r",
                    tool_elapsed,
                    lookup_title[:80],
                    result.get("attempts", ""),
                    result.get("found"),
                    result.get("article_url", ""),
                    str(result.get("reason", "") or "")[:160],
                )
            return ActionResult(
                extracted_content=json.dumps(result, ensure_ascii=False),
                include_in_memory=True,
            )

        @controller.action(
            "点击最终发布/确认发布按钮前必须调用。"
            "在页面内安装发布结果监听器，捕获 2-3 秒自动消失的 toast、弹窗、字段校验或错误提示。"
        )
        async def prepare_publish_observer(browser_session):
            tool_start = time.perf_counter()
            result = {
                "ok": False,
                "observer_installed": False,
                "signal_count": 0,
                "reason": "",
            }
            try:
                page = await browser_session.get_current_page()
                if page is None:
                    result["reason"] = "current_page_unavailable"
                else:
                    evaluated = await page.evaluate(PUBLISH_OBSERVER_SCRIPT)
                    if isinstance(evaluated, dict):
                        result.update(evaluated)
                    else:
                        result.update(
                            {
                                "ok": True,
                                "observer_installed": True,
                                "raw_result": str(evaluated or "")[:300],
                            }
                        )
            except Exception as exc:
                result["reason"] = str(exc)
            if logger:
                logger.info(
                    "[AgentTool] name=prepare_publish_observer duration=%.2fs ok=%s "
                    "installed=%s signals=%s reason=%r",
                    time.perf_counter() - tool_start,
                    result.get("ok"),
                    result.get("observer_installed"),
                    result.get("signal_count"),
                    result.get("reason", ""),
                )
            return ActionResult(
                extracted_content=json.dumps(result, ensure_ascii=False),
                include_in_memory=True,
            )

        @controller.action(
            "点击最终发布/确认发布按钮后调用，用于读取发布结果页面证据。"
            "会在 wait_seconds 秒内短轮询页面内缓存、当前弹窗、toast、字段错误、URL 和标题；"
            "本工具只返回证据，不替你判断成功或失败。"
        )
        async def observe_publish_result(wait_seconds: float = 6.0, browser_session=None):
            tool_start = time.perf_counter()
            wait_seconds = max(0.5, min(float(wait_seconds or 6.0), 12.0))
            deadline = time.perf_counter() + wait_seconds
            last_snapshot = {}
            page = None
            try:
                if browser_session is not None:
                    page = await browser_session.get_current_page()
                while time.perf_counter() < deadline:
                    if page is None and browser_session is not None:
                        page = await browser_session.get_current_page()
                    if page is not None:
                        current = await page.evaluate(PUBLISH_OBSERVATION_SCRIPT, original_title or "")
                        if isinstance(current, dict):
                            last_snapshot = current
                            normalized = normalize_observed_snapshot(
                                last_snapshot,
                                wait_seconds=wait_seconds,
                                article_title=original_title,
                            )
                            if normalized.get("signals_found"):
                                break
                    await asyncio.sleep(0.3)
            except Exception as exc:
                last_snapshot = {
                    "url": "",
                    "title": "",
                    "visible_text_excerpt": "",
                    "captured_signals": [],
                    "dialogs": [],
                    "toasts": [],
                    "form_errors": [f"observe_publish_result_failed: {exc}"],
                    "button_area_text": "",
                    "management_page_hint": False,
                    "article_title_visible": False,
                }

            normalized = normalize_observed_snapshot(
                last_snapshot,
                wait_seconds=wait_seconds,
                article_title=original_title,
            )
            if logger:
                logger.info(
                    "[AgentTool] name=observe_publish_result duration=%.2fs signals_found=%s "
                    "captured=%s dialogs=%s toasts=%s errors=%s url=%s",
                    time.perf_counter() - tool_start,
                    normalized.get("signals_found"),
                    len(normalized.get("captured_signals", [])),
                    len(normalized.get("dialogs", [])),
                    len(normalized.get("toasts", [])),
                    len(normalized.get("form_errors", [])),
                    normalized.get("url", ""),
                )
            return ActionResult(
                extracted_content=json.dumps(normalized, ensure_ascii=False),
                include_in_memory=True,
            )

        @controller.action(
            "当你根据页面原文和上下文判断这是不可恢复发布失败时必须调用。"
            "reason 优先填写页面原文，evidence 可填写 observe_publish_result 返回的关键证据；"
            "调用后任务立即以失败结束，不要继续点击或调用其他发布相关工具。"
        )
        async def finish_publish_failed(reason: str, evidence: str = "", browser_session=None):
            payload = build_terminal_failure_payload(reason=reason, evidence=evidence)
            if logger:
                logger.warning(
                    "[AgentTool] name=finish_publish_failed reason=%r evidence=%r",
                    str(reason or "")[:200],
                    str(evidence or "")[:300],
                )
            return ActionResult(
                is_done=True,
                success=False,
                extracted_content=payload,
                metadata={"publish_signal": "agent_terminal_failure"},
            )

        return controller

    @staticmethod
    async def _cleanup_awaitable_with_timeout(
        *,
        label: str,
        awaitable_factory,
        timeout_seconds: float,
        logger,
    ) -> dict:
        started = time.perf_counter()
        if logger:
            logger.info("[Cleanup] %s close_start timeout=%.1fs", label, timeout_seconds)
        try:
            await asyncio.wait_for(awaitable_factory(), timeout=timeout_seconds)
            elapsed = time.perf_counter() - started
            if logger:
                logger.info("[Cleanup] %s closed duration=%.2fs", label, elapsed)
            return {"label": label, "closed": True, "timed_out": False}
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - started
            if logger:
                logger.warning(
                    "[Cleanup] %s close_timeout duration=%.2fs background_cleanup=true",
                    label,
                    elapsed,
                )
            return {"label": label, "closed": False, "timed_out": True}
        except Exception as exc:
            elapsed = time.perf_counter() - started
            if logger:
                logger.warning(
                    "[Cleanup] %s close_failed duration=%.2fs error=%s",
                    label,
                    elapsed,
                    exc,
                )
            return {"label": label, "closed": False, "timed_out": False}

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
        from app.utils.markdown import markdown_to_html

        platform = self._platform()
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
                "   - 不要点击文章正文区域的“预览”按钮，也不要点击页面底部的“预览并发布”。\n"
                "   - 在弹出的对话框中选择本地上传。\n"
                "   - 使用 file input 上传下面这个本地文件：\n"
                f"     {cover_path}\n"
                "   - 本地上传后图片可能需要处理时间；上传后先确认图片缩略图已出现或“确定”按钮已可点击。\n"
                "   - 如果按钮暂不可点，等待 2 秒后重新检查；最多检查 3 次，不要无限等待。\n"
                "   - 确认页面上显示的是单封面预览后，再点击“确定”。\n"
            )
        else:
            cover_instruction = (
                "\n7. 封面设置：不需要手动设置封面。\n"
                "   - 如果头条自动将正文第一张图片设为封面，保留即可。\n"
                "   - 如果未自动匹配，选择'无封面'。\n"
                "   - 不要主动去打开封面选择器或上传对话框。\n"
                "   - 选择'无封面'只是封面设置步骤完成，不代表已经发布。\n"
                "   - 选择'无封面'后，如果页面只显示“预览并发布”而没有“确认发布”，说明还没有进入最终确认层。\n"
                "   - 必须先点击页面底部的“预览并发布”按钮，再点击弹出的最终确认层里的“确认发布”按钮。\n"
                "   - 只有完成“确认发布”点击后，才允许调用 get_published_article_url 工具。\n"
            )

        plain_text = platform.strip_markdown(content) if is_markdown else content
        body_probe = PlatformConfig.body_probe(content)
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

        return PublishTaskBundle(
            task=task,
            plain_text=plain_text,
            rich_html=rich_html or "",
            body_probe=body_probe,
            is_markdown=is_markdown,
            platform_name=getattr(platform, "name", ""),
        )

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

    async def _capture_page_state(
        self,
        session,
        content_probe: str = "",
    ) -> dict:
        """抓取当前页面状态用于诊断日志。

        关键改进（修 4738382 之后的 false positive）：
        之前读 `document.body.innerText` 把页面 chrome（顶栏、侧栏、封面上传区）
        当成编辑器内容，导致 editor_len 一直是 457、body_paste_succeeded 误判
        为 True。现在改为：

        1) 在主文档里找最大 [contenteditable="true"] 元素作为正文编辑器。
        2) 主文档没有富编辑器时，遍历 iframe 里的 contenteditable 兜底。
        3) 都没有才退回到 document.body（旧行为）但 editor_source 标记为
           "body-fallback"，日志里能看出来没找到。

        content_probe 透传后，JS 会检查编辑器可见文本是否包含该探针字符串
        （取自原始正文的去格式前缀），用于诊断"编辑器真的有正文吗"。
        """
        state = {
            "url": "",
            "editor_text_length": 0,
            "editor_text_preview": "",
            "editor_source": "none",
            "probe_found": False,
        }
        try:
            state["url"] = await session.get_current_page_url()
        except Exception:
            pass
        try:
            page = await session.get_current_page()
            if page is None:
                return state
            captured = await page.evaluate(
                """(contentProbe) => {
                    const probe = contentProbe || "";
                    let best = null;
                    // 1) 主文档里最大的 contenteditable
                    const editables = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                    for (const node of editables) {
                        const t = (node.innerText || node.textContent || '').trim();
                        const len = t.length;
                        if (!best || len > best.len) {
                            best = { text: t, len: len, source: 'contenteditable' };
                        }
                    }
                    // 2) 兜底：iframe 里的 contenteditable
                    if (!best || best.len < 10) {
                        const iframes = Array.from(document.querySelectorAll('iframe'));
                        for (const iframe of iframes) {
                            try {
                                const doc = iframe.contentDocument || (iframe.contentWindow && iframe.contentWindow.document);
                                if (!doc) continue;
                                const innerEditables = Array.from(doc.querySelectorAll('[contenteditable="true"]'));
                                for (const node of innerEditables) {
                                    const t = (node.innerText || node.textContent || '').trim();
                                    if (t.length > (best ? best.len : 0)) {
                                        best = { text: t, len: t.length, source: 'iframe-contenteditable' };
                                    }
                                }
                            } catch (e) { /* cross-origin, skip */ }
                        }
                    }
                    // 3) 最终兜底：document.body
                    if (!best) {
                        const body = document.body;
                        const text = body ? (body.innerText || body.textContent || '') : '';
                        best = { text: text, len: text.length, source: 'body-fallback' };
                    }
                    const probeFound = !!(probe && best && best.text && best.text.indexOf(probe) !== -1);
                    return {
                        text: best ? best.text : '',
                        length: best ? best.len : 0,
                        source: best ? best.source : 'none',
                        probeFound: probeFound,
                    };
                }""",
                content_probe,
            )
            if isinstance(captured, str):
                try:
                    parsed = json.loads(captured)
                    if isinstance(parsed, dict):
                        captured = parsed
                    else:
                        captured = {"text": captured, "length": len(captured), "source": "fallback"}
                except json.JSONDecodeError:
                    captured = {"text": captured, "length": len(captured), "source": "fallback"}
            if isinstance(captured, dict):
                text = str(captured.get("text", "") or "")
                state["editor_text_length"] = len(text)
                state["editor_text_preview"] = text[:200]
                state["editor_source"] = captured.get("source", "unknown")
                state["probe_found"] = bool(captured.get("probeFound", False))
        except Exception:
            pass
        return state

    @staticmethod
    def _extract_action_summary(history_item) -> str:
        """从 history_item 的 model_output 中抽取动作的简短文本。"""
        try:
            model_output = getattr(history_item, "model_output", None)
            if model_output is None:
                return ""
            action = getattr(model_output, "action", None)
            if action is None:
                return ""
            if isinstance(action, (list, tuple)):
                parts = [str(item)[:200] for item in action]
                return " | ".join(parts)
            return str(action)[:300]
        except Exception:
            return ""

    @staticmethod
    def _action_summary_is_done(action_summary: str) -> bool:
        normalized = str(action_summary or "").strip().lower()
        return bool(
            normalized == "done"
            or "doneactionmodel" in normalized
            or "doneaction(" in normalized
        )

    @staticmethod
    def _extract_result_summary(history_item) -> str:
        """从 history_item.result 中抽取 error / extracted_content / long_term_memory。"""
        try:
            results = getattr(history_item, "result", None) or []
            parts = []
            for result in results:
                for field_name in ("error", "extracted_content", "long_term_memory"):
                    value = getattr(result, field_name, None) if result else None
                    if value:
                        parts.append(f"{field_name}={str(value)[:200]}")
            return " | ".join(parts)[:500]
        except Exception:
            return ""

    @staticmethod
    def _looks_like_paste_action(action_summary: str) -> bool:
        """判定动作字符串是否暗示粘贴尝试。

        包含：
        - Ctrl+V 真实键事件（send_keys / keyboard.press / page.keyboard）
        - document.execCommand('insertHTML'/'insertText') fallback 路径
          （682aa49 引入，绕过系统剪贴板直接 DOM 注入）
        """
        text = (action_summary or "").lower()
        markers = (
            "control+v",
            "ctrl+v",
            "control v",
            "ctrl v",
            "send_keys",
            "keyboard.press",
            "page.keyboard",
            "inserthtml",  # execCommand('insertHTML', ...) 落点字符串
            "inserttext",  # execCommand('insertText', ...) 落点字符串
            "execcommand",  # 兜底：任何 execCommand 都算
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _looks_like_publish_action(action_summary: str) -> bool:
        text = str(action_summary or "")
        if not PublishService._looks_like_click_result(text):
            return False
        markers = (
            "预览并发布",
            "确认发布",
            "发布",
            "publish",
            "preview",
            "纭鍙戝竷",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _body_completeness_from_state(page_state: dict, expected_text_length: int) -> dict:
        editor_len = int((page_state or {}).get("editor_text_length", 0) or 0)
        expected_len = int(expected_text_length or 0)
        editor_source = str((page_state or {}).get("editor_source", "") or "")
        probe_found = bool((page_state or {}).get("probe_found", False))
        if expected_len <= 0:
            return {
                "ok": True,
                "reason": "",
                "editor_text_length": editor_len,
                "expected_text_length": expected_len,
                "probe_found": probe_found,
                "editor_source": editor_source,
            }
        min_len = int(expected_len * 0.85)
        ok = (
            editor_len >= min_len
            and probe_found
            and editor_source in {"contenteditable", "iframe-contenteditable", "textarea"}
        )
        return {
            "ok": ok,
            "reason": "" if ok else "body_incomplete",
            "editor_text_length": editor_len,
            "expected_text_length": expected_len,
            "probe_found": probe_found,
            "editor_source": editor_source,
        }

    def _log_step_diagnostics(
        self,
        step_number: int,
        history_item,
        page_state: dict,
        logger,
    ) -> None:
        """每步结束输出 [StepDiag N] 行，便于排查。"""
        url = page_state.get("url", "") or ""
        editor_len = page_state.get("editor_text_length", 0) or 0
        editor_source = page_state.get("editor_source", "none") or "none"
        probe_found = bool(page_state.get("probe_found", False))
        editor_preview = (
            (page_state.get("editor_text_preview", "") or "").replace("\n", " ")
        )[:120]
        logger.info(
            f"[StepDiag {step_number}] url={url} editor_len={editor_len} "
            f"editor_source={editor_source} probe_found={probe_found} "
            f"editor_preview={editor_preview!r}"
        )
        action_summary = self._extract_action_summary(history_item)
        if action_summary:
            logger.info(f"[StepDiag {step_number}] action: {action_summary[:400]}")
        result_summary = self._extract_result_summary(history_item)
        if result_summary:
            logger.info(f"[StepDiag {step_number}] result: {result_summary[:500]}")

    def _log_final_summary(
        self,
        publish_guard: dict,
        result: dict,
        logger,
        trace_summary: dict | None = None,
    ) -> None:
        """发布流程结束后输出最终诊断摘要：失败时尤其关键。"""
        trace_summary = trace_summary or {}
        success = bool(result.get("success"))
        step_summaries = publish_guard.get("step_summaries", []) or []
        step_count = len(step_summaries)
        last_url = publish_guard.get("last_url", "") or ""
        last_editor_len = publish_guard.get("last_editor_text_len", 0) or 0
        last_editor_source = publish_guard.get("last_editor_source", "none") or "none"
        last_probe_found = bool(publish_guard.get("last_probe_found", False))
        last_editor_preview = (
            (publish_guard.get("last_editor_preview", "") or "").replace("\n", " ")
        )[:120]
        body_paste_attempts = publish_guard.get("body_paste_attempts", 0) or 0
        body_paste_succeeded = bool(publish_guard.get("body_paste_succeeded", False))
        failure_reason = str(result.get("failure_reason", "") or "")
        article_url = str(result.get("article_url", "") or "")
        repeat_warnings = trace_summary.get("repeat_warnings", []) or []
        llm_calls = trace_summary.get("llm_calls", []) or []

        logger.info("=" * 60)
        logger.info("[FinalDiag] 发布结果诊断")
        logger.info(f"  状态: {'成功' if success else '失败'}")
        logger.info(f"  总步数: {step_count}")
        logger.info(f"  最后 URL: {last_url}")
        logger.info(f"  最后正文长度: {last_editor_len}")
        logger.info(f"  最后正文来源: {last_editor_source}")
        logger.info(f"  最后正文探针命中: {last_probe_found}")
        logger.info(f"  最后正文预览: {last_editor_preview!r}")
        logger.info(f"  失败原因: {failure_reason or '(无)'}")
        logger.info(f"  Article URL: {article_url or '(无)'}")
        logger.info(
            "[AgentFinal] status=%s steps=%s repeats=%s last_intent=%r last_action=%r article_url=%s",
            "succeeded" if success else "failed",
            step_count,
            len(repeat_warnings),
            trace_summary.get("last_intent", ""),
            trace_summary.get("last_action", ""),
            article_url or "",
        )
        if llm_calls:
            logger.info(
                "[AgentTiming] phase=llm_calls count=%d total=%.2fs avg=%.2fs max=%.2fs",
                len(llm_calls),
                sum(llm_calls),
                sum(llm_calls) / len(llm_calls),
                max(llm_calls),
            )
        if repeat_warnings:
            logger.warning("[AgentFinal] repeat_warnings=%d", len(repeat_warnings))
            for warning in repeat_warnings[-5:]:
                logger.warning("[AgentFinal] recent_repeat %s", warning)
        if body_paste_attempts > 0:
            logger.info(f"  正文粘贴尝试次数: {body_paste_attempts}")
            logger.info(f"  正文粘贴是否成功: {body_paste_succeeded}")
        if not success and step_count > 0:
            logger.info("  最近 5 步摘要:")
            for s in step_summaries[-5:]:
                action = (s.get("action", "") or "").replace("\n", " ")[:100]
                logger.info(
                    f"    Step {s.get('step', '?')}: "
                    f"url={s.get('url', '')} editor_len={s.get('editor_len', 0)} "
                    f"action={action!r}"
                )
        logger.info("=" * 60)

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
            matched = self._platform().title_matches(normalized_expected, normalized_actual)
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
        # 放宽匹配：忽略平台列表页插入的空白，并允许双向子串包含。
        matched = self._platform().title_matches(normalized_expected, normalized_actual)

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
            if article_detail.get("reason"):
                return {
                    "found": False,
                    "article_url": "",
                    "matched_title": str(article_detail.get("detail_title", "") or ""),
                    "attempts": attempt,
                    "reason": str(article_detail.get("reason", "") or ""),
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
                        result["publish_signal"] = parsed.get("publish_signal", "") or result["publish_signal"]
                        if not result["success"]:
                            result["failure_reason"] = parsed.get("failure_reason", "") or parsed.get("message", "")
                except (json.JSONDecodeError, TypeError):
                    if logger:
                        logger.warning("无法将最终结果解析为 JSON")
                    if history.is_successful():
                        result["success"] = True
                    failure_reason = self._extract_failure_reason_from_text(str(final))
                    if failure_reason:
                        result["failure_reason"] = failure_reason
                    fallback_url = self._extract_article_url_from_text(str(final))
                    if fallback_url:
                        result["article_url"] = fallback_url

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

    @staticmethod
    def _extract_failure_reason_from_text(text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return ""

        labels = (
            "失败原因：",
            "失败原因:",
            "发布失败原因：",
            "发布失败原因:",
            "Failure Reason:",
            "Failure reason:",
        )
        for label in labels:
            start = normalized.find(label)
            if start < 0:
                continue
            reason = normalized[start + len(label):].strip()
            for marker in ("\n\n", "\r\n\r\n", "当前文章状态：", "当前文章状态:", "建议：", "建议:"):
                marker_index = reason.find(marker)
                if marker_index >= 0:
                    reason = reason[:marker_index].strip()
            return " ".join(reason.split())
        return ""

    def _extract_article_url_from_text(self, text: str) -> str:
        urls = re.findall(r"https?://[^\s<>\"'，。；、)）\]]+", text or "")
        for url in urls:
            cleaned_url = url.rstrip(".,;:!?")
            if self._looks_like_toutiao_article_url(cleaned_url):
                return normalize_toutiao_article_url(cleaned_url)
        return ""

    @staticmethod
    def _looks_like_toutiao_article_url(url: str) -> bool:
        return bool(
            url
            and (
                "toutiao.com/item/" in url
                or "toutiao.com/article/" in url
                or (
                    "mp.toutiao.com/profile_v4/graphic/preview" in url
                    and "pgc_id=" in url
                )
            )
        )

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
