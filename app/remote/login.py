from __future__ import annotations

import asyncio
import glob
import os
import shutil
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from logging import LoggerAdapter
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import quote, urlparse

from app.core.config import REMOTE_PROFILE_DIR
from app.streaming import kasmvnc
from app.streaming.display_pool import DisplayPool, Slot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if TOOLS_DIR.exists() and str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

REMOTE_LOGIN_SERVICE_PORT = int(os.getenv("REMOTE_LOGIN_SERVICE_PORT", os.getenv("SERVICE_PORT", "8833")))
REMOTE_LOGIN_MAX_SESSIONS = int(os.getenv("MAX_REMOTE_LOGIN_SESSIONS", "4"))
REMOTE_LOGIN_DISPLAY_BASE = int(os.getenv("DISPLAY_BASE", "100"))
REMOTE_LOGIN_KASMVNC_PORT_BASE = int(os.getenv("KASMVNC_PORT_BASE", "6900"))
REMOTE_LOGIN_KASMVNC_BIN = os.getenv("KASMVNC_BIN", "Xvnc")
REMOTE_LOGIN_KASMVNC_WWW = os.getenv("KASMVNC_WWW", "")
REMOTE_LOGIN_VNC_SCREEN = os.getenv("REMOTE_VNC_SCREEN", "1920x1080x24")
# 登录 live url 对外暴露的公网 base（如 https://a2a.fans）。反向代理需把
# {base}/vnc/{session_id}/ 转发到本服务端口。留空则回退到本机直连地址（本地开发）。
REMOTE_PUBLIC_BASE_URL = os.getenv("REMOTE_PUBLIC_BASE_URL", "").strip().rstrip("/")

# ── 预热登录会话池 ─────────────────────────────────────────────
# 创建一个登录会话要串行拉起 Xvnc + Chrome（约 3–10s），把这部分提前在后台
# 做好、按平台维持若干空闲会话，用户请求来时直接复用，省掉进程拉起的等待。
# REMOTE_LOGIN_WARM_PER_PLATFORM=0 表示关闭预热（默认，行为与之前一致）。
# 预热会话与真实会话共享 MAX_REMOTE_LOGIN_SESSIONS 这个总容量。
REMOTE_LOGIN_WARM_PER_PLATFORM = int(os.getenv("REMOTE_LOGIN_WARM_PER_PLATFORM", "0"))
REMOTE_LOGIN_WARM_PLATFORMS = os.getenv("REMOTE_LOGIN_WARM_PLATFORMS", "toutiao,sohu")
# 维护线程巡检间隔（秒）：补水位、回收僵死/过期会话
REMOTE_LOGIN_WARM_CHECK_INTERVAL = float(os.getenv("REMOTE_LOGIN_WARM_CHECK_INTERVAL", "30"))
# 预热会话存活上限（秒）：超过则回收重建，避免登录页临时 token 过期
REMOTE_LOGIN_WARM_TTL = float(os.getenv("REMOTE_LOGIN_WARM_TTL", "600"))

# ── 登录 cookie 自动检测 ───────────────────────────────────────
# 真实登录会话起一个后台轮询，检测到平台登录 cookie 即自动完成捕获+绑定，
# 免去前端显式调用 save-cookie。save-cookie 接口保留为本启发式漏判时的兜底。
REMOTE_LOGIN_COOKIE_POLL_INTERVAL = float(os.getenv("REMOTE_LOGIN_COOKIE_POLL_INTERVAL", "3"))
# 连续命中多少次才认定登录稳定完成（防抖，避免抓到登录中途的半成品 cookie）
REMOTE_LOGIN_COOKIE_POLL_STABLE = int(os.getenv("REMOTE_LOGIN_COOKIE_POLL_STABLE", "2"))
# 单个会话自动轮询的最长时长（秒）：超过则停止轮询，仅保留手动 save-cookie 兜底，
# 避免被抛弃的会话在后台无限轮询。
REMOTE_LOGIN_COOKIE_POLL_MAX_WAIT = float(os.getenv("REMOTE_LOGIN_COOKIE_POLL_MAX_WAIT", "600"))
# 单个真实登录会话等待用户完成登录的硬上限（秒）：无论用户是否还开着 viewer，
# 超过后都回收浏览器/Xvnc，并通知上游 job 失败，避免登录会话无限占用资源。
REMOTE_LOGIN_WAITING_TTL = float(os.getenv("REMOTE_LOGIN_WAITING_TTL", "600"))

# ── 登录成功后的「宽限期」回收 ─────────────────────────────────
# 抓到 cookie 后不再立刻拆会话，而是让 viewer 多停留一会：给用户「登录成功」的
# 明确反馈、允许其自行关闭或简单查看，避免画面无征兆 fail-to-connect。会话在以下
# 三者最早命中时被回收：
#   · 空闲：无活跃 viewer WS 连接超过 IDLE 秒（用户已关掉页面）
#   · 硬上限：自捕获起最多再保留 TTL 秒（兜底「开着页面不动」导致 slot 泄漏——
#            只靠空闲判定会漏，因为 WS 一直连着就永远不空闲）
#   · 显式：调用方主动 cleanup / cancel
# 任一设为 0 即关闭对应判据。
REMOTE_LOGIN_POST_CAPTURE_TTL = float(os.getenv("REMOTE_LOGIN_POST_CAPTURE_TTL", "90"))
REMOTE_LOGIN_POST_CAPTURE_IDLE = float(os.getenv("REMOTE_LOGIN_POST_CAPTURE_IDLE", "30"))
# 宽限会话回收巡检间隔（秒）
REMOTE_LOGIN_REAP_INTERVAL = float(os.getenv("REMOTE_LOGIN_REAP_INTERVAL", "5"))

_remote_display_pool = DisplayPool(
    size=REMOTE_LOGIN_MAX_SESSIONS,
    display_base=REMOTE_LOGIN_DISPLAY_BASE,
    port_base=REMOTE_LOGIN_KASMVNC_PORT_BASE,
)

# 登录浏览器由 Playwright 启动（launch_persistent_context），DISPLAY 通过 env= 直接传给
# 子进程、各会话隔离，故不再需要旧的「设 os.environ['DISPLAY'] → start() → 还原」全局锁。


async def _launch_login_browser(
    profile_dir: str,
    display: str,
    cdp_port: int,
    proxy_dict: dict | None,
    logger,
):
    """用 Playwright 启动有头登录浏览器（注入代理 + DISPLAY），返回 (playwright, context)。

    代理注入在 Playwright 这一层（launch_persistent_context(proxy=...)，原生支持账密），
    之后由 browser-use 通过 CDP 连接做页面导航/cookie 捕获——与发文路径同模式。

    DISPLAY 通过 Playwright env= 直接传给浏览器子进程（替代旧的 os.environ 全局锁 hack），
    各会话各传各的、天然隔离，故无需 _launch_lock 串行化、可并发启动。--kiosk 全屏无壳，
    配合该显示上的 openbox 跟随桌面 resize 铺满。
    """
    from playwright.async_api import async_playwright

    width, height = _vnc_screen_window_size()
    playwright = await async_playwright().start()
    # env= 会替换整个子进程环境，必须并入当前 os.environ 再覆盖 DISPLAY，否则浏览器
    # 丢失 PATH 等必要变量。
    env = {**os.environ, "DISPLAY": display}
    if proxy_dict:
        logger.info(
            "[Proxy] 登录浏览器走代理: server=%s auth=%s",
            proxy_dict.get("server"),
            "是" if proxy_dict.get("username") else "否",
        )
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        chromium_sandbox=False,  # 容器内无 user namespace，需关沙箱
        executable_path=_find_browser_path(),
        env=env,
        proxy=proxy_dict,  # None 时 Playwright 忽略，等价直连
        viewport=None,  # 用真实窗口尺寸，不固定 viewport
        args=[
            f"--remote-debugging-port={cdp_port}",
            f"--window-size={width},{height}",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--test-type",  # 压掉"仅供自动化测试"横幅
            # browser-use 默认开"自动化优化扩展"会去 GitHub 下载、国内容器卡死；
            # 登录流用不到，且这里用 Playwright 直接启动也不会带那些扩展。
            # 全屏无壳：无地址栏（用户不能乱输 URL）、无标签栏。kiosk 需窗口管理器
            # 才能正确全屏并跟随桌面 resize——该显示上的 openbox 负责这件事。
            "--kiosk",
        ],
    )
    return playwright, context


async def _connect_login_browser(cdp_port: int):
    """browser-use 通过 CDP 连接已由 Playwright 启动的登录浏览器。

    返回连接好的 BrowserSession，供下游 navigate_to / cookies / 导航守卫复用——
    这些高层 API 与浏览器由谁启动无关，只要 CDP 连上即可。
    """
    from browser_use import BrowserSession
    from app.utils.browser import get_cdp_url

    session = BrowserSession(cdp_url=get_cdp_url(cdp_port))
    await session.connect()
    return session


async def _verify_login_exit_ip(context, proxy_info, logger) -> None:
    """校验登录浏览器实际出口 IP 是否等于代理 IP（受 verify_exit_ip 开关控制）。

    失败仅 warning 不阻断登录（避免 ip 探测站临时不可用误杀登录会话）。
    """
    from app.proxy.assignment import get_assignment_manager
    from app.proxy.verifier import ExitIPVerifier

    mgr = get_assignment_manager()
    if mgr is None or proxy_info is None or not mgr.config.defaults.verify_exit_ip:
        return
    try:
        await ExitIPVerifier().verify(
            context, proxy_info, check_url=mgr.config.defaults.exit_ip_check_url
        )
    except Exception as exc:
        if logger:
            logger.warning("[Proxy] 登录出口 IP 验证未通过（不阻断登录）: %s", exc)


# 头条等平台登录页背景是 autoplay+loop 的 <video>，持续运动会触发 KasmVNC「视频模式」
# (服务端主动降分辨率 + 4:2:0 抽样)，导致整帧——连同登录框文字——发糊。命令行 -VideoArea
# 在部分 KasmVNC 版本上禁不住视频模式；从源头消除运动最稳妥，且与 KasmVNC 版本/参数完全
# 解耦：注入脚本把所有 <video> 暂停冻结、把 play() 改成空操作，并用 MutationObserver +
# 短时轮询兜住延迟插入的视频。背景视频纯装饰，禁掉不影响登录功能。
_VIDEO_SUPPRESS_JS = r"""
(() => {
  const freeze = (v) => {
    try { v.autoplay = false; v.loop = false; v.muted = true; v.pause(); } catch (e) {}
  };
  const sweep = () => {
    try { document.querySelectorAll('video').forEach(freeze); } catch (e) {}
  };
  try {
    const proto = HTMLMediaElement.prototype;
    if (!proto.__videoSuppressed) {
      proto.__videoSuppressed = true;
      proto.play = function () { try { this.pause(); } catch (e) {} return Promise.resolve(); };
    }
  } catch (e) {}
  sweep();
  try {
    new MutationObserver(sweep).observe(document.documentElement, { childList: true, subtree: true });
  } catch (e) {}
  let n = 0;
  const t = setInterval(() => { sweep(); if (++n > 40) clearInterval(t); }, 250);
})();
"""


async def _suppress_background_video(session) -> None:
    """禁掉登录页背景视频，避免 KasmVNC 进入视频模式致整帧发糊。

    分两步、失败均不致命：
    1) Page.addScriptToEvaluateOnNewDocument —— 对之后每次文档加载(含导航守卫的重新导航)
       在脚本执行前生效，赶在 <video> autoplay 之前冻结；
    2) Runtime.evaluate —— 对「注册前可能已加载」的当前页面立即清扫一次兜底。
    注册必须在 navigate 之前调用，立即清扫在 navigate 之后调用。
    """
    try:
        cdp = await session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
            params={"source": _VIDEO_SUPPRESS_JS}, session_id=cdp.session_id
        )
    except Exception:
        pass


async def _kill_background_video_now(session) -> None:
    """对当前已加载页面立即清扫一次背景视频。失败不致命。"""
    try:
        cdp = await session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": _VIDEO_SUPPRESS_JS, "returnByValue": True},
            session_id=cdp.session_id,
        )
    except Exception:
        pass


async def _open_login_browser(session, url: str) -> None:
    """登录浏览器已由 Playwright 启动、browser-use 已 CDP 连上：导航到登录页并抑制背景视频。

    DISPLAY 已在 Playwright 启动时通过 env= 注入（不再需要旧的 os.environ 全局锁），
    故此处只做导航相关工作。
    """
    # 注册必须早于 navigate：让脚本赶在登录页 <video> autoplay 之前冻结背景视频，
    # 否则一旦视频先播放就会触发 KasmVNC 视频模式致画面发糊。
    await _suppress_background_video(session)
    await session.navigate_to(url)
    await _kill_background_video_now(session)


# ── 导航守卫（后台轮询）─────────────────────────────────────────
# 周期性检查会话浏览器当前 URL，跑出平台白名单就 navigate_to 拽回登录页。
REMOTE_LOGIN_NAV_GUARD_INTERVAL = float(os.getenv("REMOTE_LOGIN_NAV_GUARD_INTERVAL", "1.0"))
# 安装守卫后的保护期（秒）：让 login_url 自身的重定向链跑完，避免误拦白屏
REMOTE_LOGIN_NAV_GUARD_GRACE = float(os.getenv("REMOTE_LOGIN_NAV_GUARD_GRACE", "15"))
# 同一会话拦截上限：超过则停止拦截，避免"拦截→跳回→又触发→又拦截"死循环把页面搞挂
REMOTE_LOGIN_NAV_GUARD_MAX_INTERCEPTS = int(os.getenv("REMOTE_LOGIN_NAV_GUARD_MAX_INTERCEPTS", "5"))

REMOTE_LOGIN_ALLOWED_HOSTS = {
    "toutiao": (
        "toutiao.com",
        # 头条登录过程中嵌入的第三方域名（验证码、SSO、CDN 等）
        "snssdk.com",
        "byteimg.com",
        "pstatp.com",
        "bytedance.com",
        "volces.com",
        "bytegoofy.com",
        "toutiaoapi.com",
        "amemv.com",
    ),
    "sohu": (
        "sohu.com",
        # 搜狐登录过程中嵌入的第三方域名（腾讯滑块验证码、CDN 等）
        "gtimg.com",  # turing.captcha.gtimg.com（腾讯滑块验证码）
        "gtimg.cn",
        "qq.com",
        "qpic.cn",
        "qlogo.cn",
        "smtcdns.net",
        "idqqimg.com",
    ),
}


@dataclass
class RemoteLoginSession:
    session_id: str
    platform: str
    channel_id: str
    login_url: str
    status: str = "waiting_cookie"
    viewer_port: int = 0
    # browser-use BrowserSession（CDP 连接到由 Playwright 启动的有头浏览器）
    browser: object | None = None
    # Playwright 实例与 context：由本会话用 launch_persistent_context 启动，cleanup 时关闭
    playwright: object | None = None
    context: object | None = None
    stream: object | None = None
    slot: Slot | None = None
    # 会话构建/交给用户的时刻（time.monotonic()），用于等待登录 TTL 与预热 TTL 回收
    created_monotonic: float = 0.0
    # 自动检测登录 cookie 的后台轮询任务；会话销毁时取消。预热会话为 None
    cookie_watcher: object | None = None
    # 导航守卫后台轮询任务（把跑出白名单的页面拽回登录页）；会话销毁时取消
    nav_guard: object | None = None
    # ── 登录成功后的宽限期状态 ──────────────────────────────
    # 进入「已完成、待回收」宽限期的时刻（time.monotonic()）；0 表示尚未捕获 cookie
    completed_monotonic: float = 0.0
    # 当前活跃的 viewer WS 连接数（由 vnc_proxy 进出时维护）；>0 表示用户还看着
    active_connections: int = 0
    # 最近一次活跃连接清零的时刻；配合 active_connections==0 计算空闲时长
    idle_since_monotonic: float = 0.0


class RemoteLoginRunner:
    """Isolated facade for remote cookie acquisition."""

    def __init__(
        self,
        starter: Callable[[str, str, str], Awaitable[str]] | None = None,
        on_cookie_ready: Callable[[str, list[dict]], Awaitable[None]] | None = None,
        on_session_timeout: Callable[[str, str], Awaitable[None]] | None = None,
        logger_factory: Callable[[str], LoggerAdapter] | None = None,
        warm_per_platform: int | None = None,
        warm_platforms: list[str] | None = None,
    ):
        self._starter = starter
        self._on_cookie_ready = on_cookie_ready
        self._on_session_timeout = on_session_timeout
        self._logger_factory = logger_factory
        self._sessions: dict[str, RemoteLoginSession] = {}

        # ── 预热池状态 ──────────────────────────────────────────
        self._warm_target = (
            warm_per_platform if warm_per_platform is not None else REMOTE_LOGIN_WARM_PER_PLATFORM
        )
        # 启用多 IP 代理时强制关闭预热池：预热会话在 channel_id 未知时就启动浏览器，
        # 无法注入「该渠道绑定的代理 IP」。改为全部按需构建（_build_session 时已知
        # channel_id，能正确注入代理）。代理未启用时预热行为不变。
        from app.core.config import PROXY_ENABLED as _PROXY_ENABLED

        if _PROXY_ENABLED and self._warm_target > 0:
            from app.core.request_logging import get_service_logger

            get_service_logger().info(
                "多 IP 代理已启用，预热登录会话池被禁用（登录会话改为按需构建以正确绑定代理 IP）。"
            )
            self._warm_target = 0
        self._warm_platforms = (
            warm_platforms
            if warm_platforms is not None
            else _parse_warm_platforms(REMOTE_LOGIN_WARM_PLATFORMS)
        )
        self._warm_ttl = REMOTE_LOGIN_WARM_TTL
        self._warm_check_interval = REMOTE_LOGIN_WARM_CHECK_INTERVAL
        self._warm: dict[str, list[RemoteLoginSession]] = {p: [] for p in self._warm_platforms}
        self._warm_lock = asyncio.Lock()
        self._replenish_locks = {p: asyncio.Lock() for p in self._warm_platforms}
        self._replenish_tasks: set[asyncio.Task] = set()
        self._maintainer_task: asyncio.Task | None = None
        # 宽限会话回收巡检任务（独立于预热池，预热关闭时也照常运行）
        self._reaper_task: asyncio.Task | None = None

    async def start(self, platform: str, channel_id: str) -> RemoteLoginSession:
        if self._starter:
            session_id = uuid.uuid4().hex
            login_url = await self._starter(platform, channel_id, session_id)
            session = RemoteLoginSession(
                session_id=session_id,
                platform=platform,
                channel_id=channel_id,
                login_url=login_url,
                created_monotonic=time.monotonic(),
            )
            self._sessions[session_id] = session
            return session

        # 优先复用一个预热好的会话；没有再现场构建。
        warm = await self._claim_warm(platform)
        if warm is not None:
            warm.channel_id = channel_id
            warm.created_monotonic = time.monotonic()
            self._sessions[warm.session_id] = warm
            self._begin_cookie_watch(warm)
            self._schedule_replenish(platform)
            self._warm_logger().info(
                "复用预热登录会话 platform=%s session_id=%s", platform, warm.session_id
            )
            return warm

        try:
            session = await self._build_session(platform, channel_id)
        except RuntimeError as exc:
            # 容量打满且有预热会话占着坑：腾一个出来再试，保证真实请求不被预热饿死。
            if "capacity reached" in str(exc) and await self._evict_one_warm():
                session = await self._build_session(platform, channel_id)
            else:
                raise
        self._sessions[session.session_id] = session
        self._begin_cookie_watch(session)
        self._schedule_replenish(platform)
        return session

    def get(self, session_id: str) -> RemoteLoginSession | None:
        return self._sessions.get(session_id)

    async def complete_with_cookies(self, session_id: str, cookies: list[dict]) -> bool:
        # Cookie 不在这里落盘：捕获后交给 on_cookie_ready（orchestrator）去做
        # 渠道绑定/去重/校验，保证绑定逻辑只有一处。
        session = self._sessions[session_id]
        if session.status == "completed":
            return False
        session.status = "completed"
        self._enter_linger(session)
        if self._on_cookie_ready:
            await self._on_cookie_ready(session_id, cookies)
        return True

    def _enter_linger(self, session: RemoteLoginSession) -> None:
        """Cookie 已捕获：进入宽限期等待回收，而非立即销毁。给 viewer 留出「登录成功」
        的反馈与自行关闭的窗口；真正的拆会话交给 _reap_lingering_loop。幂等。"""
        now = time.monotonic()
        session.completed_monotonic = now
        # 此刻起按「无人连着」计空闲：若用户仍连着，active_connections>0 不会被判空闲，
        # 由 TTL 兜底；用户关掉页面后 vnc_proxy 会把计数清零并刷新 idle_since。
        session.idle_since_monotonic = now

    def _begin_cookie_watch(self, session: RemoteLoginSession) -> None:
        """为真实登录会话起一个后台轮询：检测到平台登录 cookie 即自动绑定。

        预热会话（无人登录、channel 还没绑）不在此列——只在 start() 把会话交给
        真实用户时调用。注入式 starter（无真实浏览器）也跳过。幂等。
        """
        if session.browser is None or session.cookie_watcher is not None:
            return
        session.cookie_watcher = asyncio.create_task(
            self._watch_login_cookies(session.session_id)
        )

    async def _watch_login_cookies(self, session_id: str) -> None:
        """后台轮询登录会话浏览器：一旦平台登录 cookie 连续稳定出现，即自动捕获
        并绑定（等价于自动调用 save-cookie）。

        与手动 save-cookie 共用 complete_with_cookies → on_cookie_ready 路径，
        绑定/去重逻辑只有一处；二者经 status==completed 天然互斥。手动接口保留
        为本启发式漏判（如某平台登录 cookie 时序怪异）时的兜底。
        """
        interval = REMOTE_LOGIN_COOKIE_POLL_INTERVAL
        stable_needed = max(1, REMOTE_LOGIN_COOKIE_POLL_STABLE)
        deadline = time.monotonic() + REMOTE_LOGIN_COOKIE_POLL_MAX_WAIT
        logger = self._logger(session_id)
        stable_hits = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            session = self._sessions.get(session_id)
            if session is None or session.status != "waiting_cookie":
                return  # 会话已完成/失败/被销毁
            try:
                cookies = await self._extract_session_cookies(session)
            except Exception:
                continue  # 浏览器尚未就绪或临时读取失败，下个周期再试
            if not _has_platform_cookie(session.platform, cookies):
                stable_hits = 0
                continue
            # 防抖：连续多次命中才认定登录稳定完成，避免抓到登录中途的半成品 cookie。
            stable_hits += 1
            if stable_hits < stable_needed:
                continue
            logger.info(
                "自动检测到登录 cookie，开始绑定 session_id=%s cookie_count=%s",
                session_id,
                len(cookies),
            )
            try:
                # 已确认 cookie 合法，直接复用手中 cookie 走绑定。绑定后不立即拆会话，
                # 而是进入宽限期（complete_with_cookies→_enter_linger），由回收巡检
                # 按空闲/TTL 拆除；本轮询任务随后自然返回。
                await self.complete_with_cookies(session_id, cookies)
            except Exception as exc:
                # 自动绑定失败不致命：保留会话，用户仍可手动 save-cookie 兜底。
                logger.warning("自动保存登录 cookie 失败，等待手动兜底: %s", exc)
            return
        logger.info("登录 cookie 自动轮询超时停止，仅保留手动兜底 session_id=%s", session_id)

    async def save_session_cookies(self, session_id: str, notify: bool = True) -> list[dict]:
        session = self._sessions[session_id]
        if session.status == "completed":
            return []
        cookies = await self._extract_session_cookies(session)
        if not _has_platform_cookie(session.platform, cookies):
            session.status = "failed"
            raise RuntimeError("remote cookie invalid")
        if notify:
            await self.complete_with_cookies(session_id, cookies)
        else:
            # 手动保存路径：不通知 on_cookie_ready，由调用方拿到 cookies 后自行
            # 调 resume_after_cookie 完成绑定。
            session.status = "completed"
            self._enter_linger(session)
        # 不立即拆会话：进入宽限期，由回收巡检按空闲/TTL 拆除（见 _enter_linger）。
        return cookies

    async def _extract_session_cookies(self, session: RemoteLoginSession) -> list[dict]:
        browser = session.browser
        if browser is None:
            raise RuntimeError("remote browser unavailable")
        # browser-use 0.12 的 cookies() 经 CDP Storage.getCookies 返回原始 dict；
        # 统一归一为普通 dict，供 _has_platform_cookie / 下游 normalize 消费。
        raw = await browser.cookies()
        return [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in raw]

    async def _build_session(self, platform: str, channel_id: str) -> RemoteLoginSession:
        """现场构建一个完整登录会话：占 slot → 起 Xvnc → 按渠道取代理 → 用 Playwright 起
        有头浏览器（注入代理 + DISPLAY）→ browser-use CDP 连接 → goto 登录页 → 装导航守卫。

        预热会话 channel_id="" 不绑代理（直连）；但启用代理时预热池已被禁用（见 start_warm_pool），
        故真实登录会话总是带着已知 channel_id 进来、能正确注入代理。
        """
        session_id = uuid.uuid4().hex
        slot = await _remote_display_pool.acquire()
        stream = None
        browser = None
        playwright = None
        context = None
        profile_dir = REMOTE_PROFILE_DIR / session_id
        logger = self._logger(session_id)
        try:
            # 按渠道获取代理（channel_id="" 或代理未启用时返回 (None, None)，直连）。
            # 严格模式：代理获取失败会向上抛出，不 fallback 直连。
            from app.proxy.browser import get_channel_proxy

            proxy_dict, proxy_info = await get_channel_proxy(channel_id)

            stream = await kasmvnc.start_stream(
                slot,
                kasmvnc_bin=REMOTE_LOGIN_KASMVNC_BIN,
                screen=REMOTE_LOGIN_VNC_SCREEN,
                www_dir=REMOTE_LOGIN_KASMVNC_WWW,
            )
            login_url = _login_url_for(platform)
            if profile_dir.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
            profile_dir.mkdir(parents=True, exist_ok=True)

            cdp_port = _find_free_port()
            playwright, context = await _launch_login_browser(
                str(profile_dir), slot.display, cdp_port, proxy_dict, logger
            )
            # 出口 IP 验证（在 browser-use 连接前用 Playwright context 直接验，最省事）。
            await _verify_login_exit_ip(context, proxy_info, logger)

            browser = await _connect_login_browser(cdp_port)
            await _open_login_browser(browser, login_url)
            session = RemoteLoginSession(
                session_id=session_id,
                platform=platform,
                channel_id=channel_id,
                login_url=_build_vnc_url(_public_base_url(), session_id),
                viewer_port=slot.web_port,
                browser=browser,
                playwright=playwright,
                context=context,
                stream=stream,
                slot=slot,
                created_monotonic=time.monotonic(),
            )
            session.nav_guard = asyncio.create_task(
                _run_navigation_guard(session, platform, login_url, self._logger(session_id))
            )
            return session
        except Exception:
            if browser is not None:
                try:
                    await browser.kill()
                except Exception:
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass
            if stream:
                try:
                    await stream.stop()
                except Exception:
                    pass
            await _remote_display_pool.release(slot)
            if profile_dir.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
            raise

    async def cleanup(self, session_id: str) -> None:
        # 先从 _sessions 摘除再拆：拆除瞬间上游 Xvnc 已死，若条目还留着，vnc_proxy
        # 会把请求转给死掉的上游而抛 500；摘除后 get() 返回 None，代理直接给 404。
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        await self._destroy(session)

    def note_viewer_connect(self, session_id: str) -> None:
        """vnc_proxy 在一条 viewer WS 建链成功后调用：标记有人正在看。"""
        session = self._sessions.get(session_id)
        if session is not None:
            session.active_connections += 1

    def note_viewer_disconnect(self, session_id: str) -> None:
        """vnc_proxy 在一条 viewer WS 断开后调用：计数清零时刷新空闲起点。"""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.active_connections = max(0, session.active_connections - 1)
        if session.active_connections == 0:
            session.idle_since_monotonic = time.monotonic()

    async def _destroy(self, session: RemoteLoginSession) -> None:
        """彻底拆掉一个会话：取消后台轮询任务 → kill 浏览器 → 停 Xvnc →
        释放 slot → 删 profile。幂等。"""
        for attr in ("cookie_watcher", "nav_guard"):
            task = getattr(session, attr, None)
            if task is not None:
                setattr(session, attr, None)
                # 若 _destroy 由该任务自身触发（自动绑定后 cleanup），不取消自己，
                # 否则会给当前任务抛 CancelledError 打断收尾。
                if task is not asyncio.current_task() and not task.done():
                    task.cancel()
        if session.browser is not None:
            try:
                await session.browser.kill()
            except Exception:
                pass
        # 关闭由本会话用 Playwright 启动的 context 与实例（kill() 走 CDP 关进程，
        # 但 Playwright 端的 context/driver 需各自显式关闭以释放资源）。
        if getattr(session, "context", None) is not None:
            try:
                await session.context.close()
            except Exception:
                pass
        if getattr(session, "playwright", None) is not None:
            try:
                await session.playwright.stop()
            except Exception:
                pass
        if session.stream:
            try:
                await session.stream.stop()
            except Exception:
                pass
        if session.slot:
            try:
                await _remote_display_pool.release(session.slot)
            except Exception:
                pass
        profile_dir = REMOTE_PROFILE_DIR / session.session_id
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 宽限会话回收：登录成功后保留一段时间，按空闲/TTL 拆除
    # ------------------------------------------------------------------
    async def start_session_reaper(self) -> None:
        """在 app 启动时调用，起一个常驻巡检回收进入宽限期的登录会话。与预热池相互
        独立——预热默认关闭，但宽限回收始终需要。幂等。"""
        if self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reap_lingering_loop())

    async def _reap_lingering_loop(self) -> None:
        while True:
            try:
                await self._reap_lingering_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warm_logger().warning("宽限会话回收巡检异常: %s", exc)
            await asyncio.sleep(REMOTE_LOGIN_REAP_INTERVAL)

    async def _reap_lingering_once(self) -> None:
        now = time.monotonic()
        expired: list[tuple[str, str]] = []
        for session_id, session in list(self._sessions.items()):
            waiting_reason = self._waiting_expiry_reason(session, now)
            if waiting_reason:
                expired.append((session_id, waiting_reason))
                continue
            # 回收已捕获 cookie、进入宽限期的会话。
            if session.status != "completed" or not session.completed_monotonic:
                continue
            reason = self._linger_expiry_reason(session, now)
            if reason:
                expired.append((session_id, reason))
        for session_id, reason in expired:
            self._warm_logger().info(
                "回收登录会话 session_id=%s reason=%s", session_id, reason
            )
            await self.cleanup(session_id)
            if reason == "waiting_cookie_ttl" and self._on_session_timeout:
                try:
                    await self._on_session_timeout(session_id, reason)
                except Exception as exc:
                    self._warm_logger().warning(
                        "通知登录会话超时失败 session_id=%s: %s", session_id, exc
                    )

    def _waiting_expiry_reason(self, session: RemoteLoginSession, now: float) -> str:
        """判定等待用户登录的会话是否到达硬上限。"""
        if session.status != "waiting_cookie":
            return ""
        if REMOTE_LOGIN_WAITING_TTL <= 0 or not session.created_monotonic:
            return ""
        if now - session.created_monotonic > REMOTE_LOGIN_WAITING_TTL:
            return "waiting_cookie_ttl"
        return ""

    def _linger_expiry_reason(self, session: RemoteLoginSession, now: float) -> str:
        """判定一个宽限会话是否到期回收，返回原因（ttl/idle）或空串。"""
        if (
            REMOTE_LOGIN_POST_CAPTURE_TTL > 0
            and now - session.completed_monotonic > REMOTE_LOGIN_POST_CAPTURE_TTL
        ):
            return "ttl"
        if (
            session.active_connections <= 0
            and REMOTE_LOGIN_POST_CAPTURE_IDLE > 0
            and now - session.idle_since_monotonic > REMOTE_LOGIN_POST_CAPTURE_IDLE
        ):
            return "idle"
        return ""

    # ------------------------------------------------------------------
    # 预热池：后台维持每平台若干空闲会话，按需直接复用
    # ------------------------------------------------------------------
    async def start_warm_pool(self) -> None:
        """在 app 启动时调用。预热关闭（target<=0）或无可预热平台时为空操作。"""
        if self._warm_target <= 0 or not self._warm_platforms:
            return
        if self._maintainer_task is not None:
            return
        self._warm_logger().info(
            "启动预热登录会话池 platforms=%s per_platform=%s",
            ",".join(self._warm_platforms),
            self._warm_target,
        )
        # 巡检循环首轮会立刻补满，无需在这里阻塞启动。
        self._maintainer_task = asyncio.create_task(self._maintain_warm_pool_loop())

    async def shutdown(self) -> None:
        """关停维护循环、拆掉所有预热会话。"""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None
        if self._maintainer_task is not None:
            self._maintainer_task.cancel()
            try:
                await self._maintainer_task
            except (asyncio.CancelledError, Exception):
                pass
            self._maintainer_task = None
        async with self._warm_lock:
            buckets = [s for bucket in self._warm.values() for s in bucket]
            for key in self._warm:
                self._warm[key] = []
        for session in buckets:
            await self._destroy(session)

    async def _maintain_warm_pool_loop(self) -> None:
        while True:
            try:
                await self._prune_and_fill()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warm_logger().warning("预热池巡检异常: %s", exc)
            await asyncio.sleep(self._warm_check_interval)

    async def _prune_and_fill(self) -> None:
        for platform in self._warm_platforms:
            # 回收僵死/过期会话
            async with self._warm_lock:
                bucket = self._warm.get(platform, [])
                alive = [s for s in bucket if self._warm_session_alive(s)]
                dead = [s for s in bucket if not self._warm_session_alive(s)]
                self._warm[platform] = alive
            for session in dead:
                self._warm_logger().info(
                    "回收僵死/过期预热会话 platform=%s session_id=%s", platform, session.session_id
                )
                await self._destroy(session)
            # 补到目标水位
            await self._replenish(platform)

    async def _replenish(self, platform: str) -> None:
        if self._warm_target <= 0 or platform not in self._warm_platforms:
            return
        lock = self._replenish_locks.get(platform)
        if lock is None or lock.locked():
            return  # 已有补水位在进行，跳过避免超额
        async with lock:
            while self._warm_count(platform) < self._warm_target:
                try:
                    session = await self._build_session(platform, channel_id="")
                except Exception as exc:
                    # 容量打满或构建失败：停手，下次巡检再试，把坑让给真实请求。
                    self._warm_logger().warning(
                        "预热会话构建中止 platform=%s: %s", platform, exc
                    )
                    break
                async with self._warm_lock:
                    self._warm.setdefault(platform, []).append(session)
                self._warm_logger().info(
                    "预热登录会话已就绪 platform=%s count=%s", platform, self._warm_count(platform)
                )

    def _schedule_replenish(self, platform: str) -> None:
        if self._warm_target <= 0 or platform not in self._warm_platforms:
            return
        task = asyncio.create_task(self._replenish(platform))
        self._replenish_tasks.add(task)
        task.add_done_callback(self._replenish_tasks.discard)

    async def _claim_warm(self, platform: str) -> RemoteLoginSession | None:
        if self._warm_target <= 0:
            return None
        dead: list[RemoteLoginSession] = []
        chosen: RemoteLoginSession | None = None
        async with self._warm_lock:
            bucket = self._warm.get(platform) or []
            while bucket:
                session = bucket.pop()
                if self._warm_session_alive(session):
                    chosen = session
                    break
                dead.append(session)
        for session in dead:
            asyncio.create_task(self._destroy(session))
        return chosen

    async def _evict_one_warm(self) -> bool:
        """容量打满时腾一个预热会话出来给真实请求。返回是否腾到了。"""
        victim: RemoteLoginSession | None = None
        async with self._warm_lock:
            for bucket in self._warm.values():
                if bucket:
                    victim = bucket.pop()
                    break
        if victim is None:
            return False
        await self._destroy(victim)
        return True

    def _warm_count(self, platform: str) -> int:
        return len(self._warm.get(platform, []))

    def _warm_session_alive(self, session: RemoteLoginSession) -> bool:
        browser = session.browser
        # browser-use 0.12：用 CDP WebSocket 连接状态判活，等价于"浏览器进程还在"。
        if browser is None or not getattr(browser, "is_cdp_connected", False):
            return False
        stream = session.stream
        if stream is not None:
            for proc in getattr(stream, "procs", []):
                if proc.returncode is not None:
                    return False
        if self._warm_ttl > 0 and session.created_monotonic:
            if time.monotonic() - session.created_monotonic > self._warm_ttl:
                return False
        return True

    def _warm_logger(self):
        from app.core.request_logging import get_service_logger

        return get_service_logger()

    def _logger(self, session_id: str):
        if self._logger_factory:
            return self._logger_factory(session_id)

        class NullLogger:
            def info(self, *args, **kwargs): pass
            def warning(self, *args, **kwargs): pass
            def error(self, *args, **kwargs): pass
            def exception(self, *args, **kwargs): pass

        return NullLogger()


def _public_base_url() -> str:
    """对外暴露 live url 的 base。配置了 REMOTE_PUBLIC_BASE_URL（如 https://a2a.fans）
    就用它；否则回退到本机直连地址，方便本地开发直接打开。"""
    if REMOTE_PUBLIC_BASE_URL:
        return REMOTE_PUBLIC_BASE_URL
    return f"http://127.0.0.1:{REMOTE_LOGIN_SERVICE_PORT}"


def _build_vnc_url(base_url: str, session_id: str) -> str:
    # 鉴权完全依赖 session_id 不可猜（uuid4 的 32 位十六进制 = 128 bit 随机），URL 本身即凭证，
    # 无需额外 token；vnc_proxy 只校验「会话存在」。
    #
    # reconnect/reconnect_delay：noVNC 客户端在连接断开后自动重连（2s），用户无需手动
    # 在面板点 Connect——尤其是空闲时被中间链路掐断的情况，对非技术用户更友好。
    #
    # 保留 KasmVNC 默认的 remote-resize（桌面跟随浏览器视口）：配合该会话显示上的
    # openbox + Chromium --kiosk，桌面=窗口=视口、1:1 像素，最清晰且铺满。
    ws_path = quote(f"vnc/{session_id}/websockify", safe="")
    return (
        f"{base_url.rstrip('/')}/vnc/{session_id}/"
        f"?path={ws_path}"
        f"&reconnect=true&reconnect_delay=2000"
    )


def _find_free_port() -> int:
    """在配置的端口范围内找一个空闲的 CDP 调试端口（默认 9000-9999）。

    Chrome 以 --remote-debugging-port 绑定 127.0.0.1，本服务也只从
    127.0.0.1 连它，纯本机回环、不经过防火墙。限定一个固定范围只是为了
    便于排查/避免与服务端口(8833)等已占用端口冲突，而非防火墙白名单要求。
    通过环境变量 CDP_PORT_RANGE 可配置范围。
    """
    port_range = os.getenv("CDP_PORT_RANGE", "9000-9999")
    try:
        start, end = map(int, port_range.split("-"))
    except (ValueError, AttributeError):
        start, end = 9000, 9999

    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in range {port_range}")


def _find_browser_path() -> str:
    configured = os.getenv("BROWSER_EXECUTABLE_PATH")
    if configured and os.path.exists(configured):
        return configured
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    playwright_bundles = sorted(
        glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome")
        + glob.glob("/ms-playwright/chromium-*/chrome-linux64/chrome")
        + glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
        + glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")
    )
    for path in playwright_bundles:
        if os.path.exists(path):
            return path
    found = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chrome")
        or shutil.which("msedge")
    )
    if found:
        return found
    raise RuntimeError("未找到 Chrome 或 Edge 浏览器")


def _vnc_screen_window_size() -> tuple[int, int]:
    """从 REMOTE_VNC_SCREEN（形如 "1920x1080x24"）解析出浏览器窗口宽高。

    与 KasmVNC 显示几何严格一致，避免窗口小于画布留黑边。解析失败回退 1920x1080。
    """
    parts = REMOTE_LOGIN_VNC_SCREEN.split("x")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return 1920, 1080


async def _run_navigation_guard(session, platform: str, login_url: str, logger) -> None:
    """后台轮询会话浏览器当前 URL，跑出平台白名单就 navigate_to 拽回登录页。

    取代旧的 page.on("framenavigated") 事件钩子：browser-use 0.12 高层 API 不暴露稳定
    的 Playwright page 事件，改用轮询 get_current_page_url() + navigate_to() 实现，版本
    鲁棒、与 cookie 轮询同模式。沿用两个保护：
    - grace period：装载后 15s 内不拦截，让 login_url 自身的重定向链跑完，避免误拦白屏；
    - 拦截上限：同一会话拦截超过上限即停手，避免"拦截→跳回→又触发→又拦截"死循环。
    """
    grace_until = time.monotonic() + REMOTE_LOGIN_NAV_GUARD_GRACE
    last_intercepted = ""
    intercepts = 0
    while True:
        await asyncio.sleep(REMOTE_LOGIN_NAV_GUARD_INTERVAL)
        browser = getattr(session, "browser", None)
        if browser is None or session.status != "waiting_cookie":
            return  # 会话已完成/失败/被销毁
        if time.monotonic() < grace_until:
            continue
        try:
            current_url = await browser.get_current_page_url()
        except Exception:
            continue  # 浏览器尚未就绪或临时读取失败，下个周期再试
        if _is_remote_login_url_allowed(platform, current_url):
            continue
        # 防止同一 URL 被反复拦截
        if current_url == last_intercepted:
            continue
        last_intercepted = current_url
        intercepts += 1
        if intercepts > REMOTE_LOGIN_NAV_GUARD_MAX_INTERCEPTS:
            if logger:
                logger.warning(
                    "远程登录 URL 守卫达到拦截上限，停止拦截 current_url=%s", current_url
                )
            return
        if logger:
            logger.warning("远程登录 URL 被拦截 platform=%s url=%s", platform, current_url)
        try:
            await browser.navigate_to(login_url)
        except Exception as exc:
            if logger:
                logger.warning("远程登录 URL 守卫跳回 login_url 失败: %s", exc)


def _is_remote_login_url_allowed(platform: str, url: str) -> bool:
    if not url:
        return True
    if url.startswith(("about:", "chrome:", "devtools:")):
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    allowed_hosts = REMOTE_LOGIN_ALLOWED_HOSTS.get(platform, ())
    return any(host == allowed_host or host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts)


def _login_url_for(platform: str) -> str:
    if platform == "toutiao":
        return "https://mp.toutiao.com/auth/page/login"
    if platform == "sohu":
        return "https://mp.sohu.com"
    raise ValueError(f"未知平台: {platform}")


def _parse_warm_platforms(raw: str) -> list[str]:
    """解析 REMOTE_LOGIN_WARM_PLATFORMS（逗号分隔），只保留有登录 URL 的平台。"""
    result: list[str] = []
    for name in (raw or "").split(","):
        name = name.strip()
        if not name or name in result:
            continue
        try:
            _login_url_for(name)
        except ValueError:
            continue
        result.append(name)
    return result


def _has_platform_cookie(platform: str, cookies: list[dict]) -> bool:
    # 登录态判定收敛到平台 config（registry），避免在这里重复维护各平台的
    # 域名 / cookie 名清单。
    from app.platforms import registry

    config = registry.config_for(platform)
    return bool(config and config.has_login_cookie(cookies))
