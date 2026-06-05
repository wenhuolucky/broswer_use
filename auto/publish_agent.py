from __future__ import annotations

from auto.adapters.publish_service import PublishServiceAdapter
from auto.cookie_store import CookieStore
from auto.job_store import JobStore
from auto.logging_config import setup_job_logger
from auto.models import (
    AutoPublishRequest,
    AutoPublishResponse,
    STATUS_CHECKING_COOKIE,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
from auto.remote_cookie.remote_login_runner import RemoteLoginRunner
from auto.settings import LOG_DIR


class PublishAgent:
    """Coordinates cookie lookup, remote login, and publishing."""

    def __init__(
        self,
        job_store: JobStore | None = None,
        cookie_store: CookieStore | None = None,
        publish_adapter: PublishServiceAdapter | None = None,
        remote_runner: RemoteLoginRunner | None = None,
        log_dir=None,
    ):
        self.job_store = job_store or JobStore()
        self.cookie_store = cookie_store or CookieStore()
        self.publish_adapter = publish_adapter or PublishServiceAdapter()
        self.remote_runner = remote_runner
        self.log_dir = log_dir or LOG_DIR

    async def submit(self, request: AutoPublishRequest) -> AutoPublishResponse:
        job = self.job_store.create(request.model_dump())
        logger, log_path = setup_job_logger(job.job_id, self.log_dir)
        self.job_store.update(job.job_id, log_file_path=str(log_path))
        logger.info(
            "收到自动发文请求 user_id=%s platform=%s title=%s content_length=%s",
            request.user_id,
            request.platform,
            request.title,
            len(request.content),
        )
        self.job_store.update(job.job_id, status=STATUS_CHECKING_COOKIE)

        if self.cookie_store.has_valid_cookie(request.platform, request.user_id):
            logger.info("Cookie 存在且有效，直接进入发文流程")
            return await self._publish_with_cookie(job.job_id, request)

        logger.info("Cookie 不存在或无效，准备启动远程登录")
        return await self._start_remote_login(job.job_id, request, "Cookie 不存在或无效")

    async def _start_remote_login(
        self,
        job_id: str,
        request: AutoPublishRequest,
        reason: str,
        mark_cookie_refresh_attempted: bool = False,
    ) -> AutoPublishResponse:
        logger, _ = setup_job_logger(job_id, self.log_dir)
        job = self.job_store.get(job_id)
        log_file_path = job.log_file_path if job else ""
        self.job_store.update(job.job_id, status=STATUS_STARTING_REMOTE_LOGIN)
        if mark_cookie_refresh_attempted:
            payload = dict(job.payload)
            payload["cookie_refresh_attempted"] = True
            self.job_store.update(job_id, payload=payload)
        if self.remote_runner is None:
            self.remote_runner = RemoteLoginRunner(
                save_cookie=self.cookie_store.save,
                on_cookie_ready=self._resume_for_remote_session,
                logger_factory=lambda _session_id, job_id=job_id: setup_job_logger(job_id, self.log_dir)[0],
            )
        try:
            session = await self.remote_runner.start(request.platform, request.user_id)
        except Exception as exc:
            logger.exception("远程登录启动失败: %s", exc)
            self.job_store.update(job_id, status=STATUS_FAILED, error=str(exc))
            return AutoPublishResponse(
                code=500,
                job_id=job_id,
                status=STATUS_FAILED,
                message=f"远程登录启动失败: {exc}",
                log_file_path=log_file_path,
            )
        self.job_store.update(
            job_id,
            status=STATUS_WAITING_COOKIE,
            login_url=session.login_url,
            remote_session_id=session.session_id,
        )
        logger.info(
            "远程登录已启动 reason=%s session_id=%s login_url=%s",
            reason,
            session.session_id,
            session.login_url,
        )
        return AutoPublishResponse(
            code=202,
            job_id=job_id,
            status=STATUS_WAITING_COOKIE,
            message="请打开 login_url 完成登录，登录成功后系统可继续发文",
            login_url=session.login_url,
            log_file_path=log_file_path,
        )

    async def _publish_with_cookie(self, job_id: str, request: AutoPublishRequest) -> AutoPublishResponse:
        logger, _ = setup_job_logger(job_id, self.log_dir)
        job = self.job_store.get(job_id)
        log_file_path = job.log_file_path if job else ""
        self.job_store.update(job_id, status=STATUS_PUBLISHING)
        logger.info("开始调用自动化发文服务")
        cookie = self.cookie_store.load_storage_state_text(request.platform, request.user_id)
        try:
            result = await self.publish_adapter.publish(
                title=request.title,
                content=request.content,
                cookie=cookie,
                request_id=job_id,
                cover_image_url=request.cover_image_url,
            )
        except Exception as exc:
            logger.exception("自动化发文服务异常: %s", exc)
            if self._looks_like_login_required({"failure_reason": str(exc)}) and not self._cookie_refresh_attempted(job):
                logger.info("自动化发文异常疑似 Cookie 失效，切换远程登录")
                return await self._start_remote_login(
                    job_id,
                    request,
                    "自动化发文异常疑似 Cookie 失效",
                    mark_cookie_refresh_attempted=True,
                )
            self.job_store.update(job_id, status=STATUS_FAILED, error=str(exc))
            return AutoPublishResponse(
                code=500,
                job_id=job_id,
                status=STATUS_FAILED,
                message=str(exc),
                log_file_path=log_file_path,
            )

        status = STATUS_SUCCEEDED if result.get("success") else STATUS_FAILED
        if status == STATUS_FAILED and self._looks_like_login_required(result) and not self._cookie_refresh_attempted(job):
            logger.info("检测到 Cookie 可能失效，切换远程登录 failure_reason=%s", result.get("failure_reason", ""))
            return await self._start_remote_login(
                job_id,
                request,
                "自动化发文检测到 Cookie 失效",
                mark_cookie_refresh_attempted=True,
            )
        self.job_store.update(job_id, status=status, result=result, error=result.get("failure_reason", ""))
        logger.info(
            "自动化发文结束 status=%s success=%s article_url=%s failure_reason=%s",
            status,
            result.get("success"),
            result.get("article_url", ""),
            result.get("failure_reason", ""),
        )
        return AutoPublishResponse(
            code=200 if status == STATUS_SUCCEEDED else 500,
            job_id=job_id,
            status=status,
            message=result.get("failure_reason", "") or result.get("message", ""),
            log_file_path=log_file_path,
            result=result,
        )

    async def resume_after_cookie(self, job_id: str, cookies: list[dict]) -> AutoPublishResponse:
        job = self.job_store.get(job_id)
        if job is None:
            return AutoPublishResponse(code=404, job_id=job_id, status=STATUS_FAILED, message="job not found")

        logger, _ = setup_job_logger(job_id, self.log_dir)
        request = AutoPublishRequest(**job.payload)
        logger.info("收到远程登录 Cookie 回调 cookie_count=%s", len(cookies))
        self.cookie_store.save(request.platform, request.user_id, cookies)
        if not self.cookie_store.has_valid_cookie(request.platform, request.user_id):
            logger.error("远程 Cookie 无效")
            self.job_store.update(job_id, status=STATUS_FAILED, error="remote cookie invalid")
            return AutoPublishResponse(
                code=400,
                job_id=job_id,
                status=STATUS_FAILED,
                message="remote cookie invalid",
                log_file_path=job.log_file_path,
            )

        logger.info("远程 Cookie 已保存且验证通过，继续发文")
        return await self._publish_with_cookie(job_id, request)

    async def _resume_for_remote_session(self, session_id: str, cookies: list[dict]) -> None:
        job = self.job_store.find_by_remote_session(session_id)
        if job is None:
            return
        await self.resume_after_cookie(job.job_id, cookies)

    def _cookie_refresh_attempted(self, job) -> bool:
        return bool(job and (job.payload or {}).get("cookie_refresh_attempted"))

    def _looks_like_login_required(self, result: dict) -> bool:
        if result.get("login_required") is True:
            return True
        text = " ".join(
            str(result.get(key, "") or "")
            for key in ("failure_reason", "message", "error", "page_url", "url")
        ).lower()
        markers = (
            "未登录",
            "登录",
            "登陆",
            "cookie 无效",
            "cookie失效",
            "cookie 已失效",
            "cookie已失效",
            "login",
            "auth/page/login",
        )
        return any(marker.lower() in text for marker in markers)
