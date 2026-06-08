from __future__ import annotations

import asyncio

from app.publishing.adapter import PublishServiceAdapter
from app.cookies.store import CookieStore
from app.jobs.store import JobStore
from app.core.job_logging import setup_job_logger
from app.jobs.models import (
    AutoPublishRequest,
    AutoPublishResponse,
    AutoTaskCreateResponse,
    LoginRequest,
    STATUS_CHECKING_COOKIE,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
from app.remote.login import RemoteLoginRunner
from app.core.config import LOG_DIR


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
        self._background_tasks: set[asyncio.Task] = set()

    async def submit(self, request: AutoPublishRequest) -> AutoTaskCreateResponse:
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
            logger.info("Cookie 存在且有效，创建后台发布任务")
            self._schedule_publish(job.job_id, request)
            return self._task_response(
                job.job_id,
                "running",
                "任务创建成功，发布任务正在后台执行",
                log_file_path=str(log_path),
            )

        logger.info("Cookie 不存在或无效，准备启动远程登录")
        login_response = await self._start_remote_login(job.job_id, request, "Cookie 不存在或无效")
        if login_response.code >= 500:
            return self._task_response(
                job.job_id,
                "failed",
                "任务创建失败",
                code=500,
                log_file_path=str(log_path),
                reason=login_response.message,
            )

        current_job = self.job_store.get(job.job_id)
        return self._task_response(
            job.job_id,
            "login_required",
            "任务创建成功，需要用户登录",
            login_url=current_job.login_url if current_job else "",
            remote_session_id=current_job.remote_session_id if current_job else "",
            log_file_path=current_job.log_file_path if current_job else str(log_path),
        )

    async def start_login_only(self, request: LoginRequest) -> AutoTaskCreateResponse:
        payload = request.model_dump()
        payload["job_type"] = "login_only"
        job = self.job_store.create(payload)
        logger, log_path = setup_job_logger(job.job_id, self.log_dir)
        self.job_store.update(job.job_id, log_file_path=str(log_path))
        logger.info("收到仅登录请求 user_id=%s platform=%s", request.user_id, request.platform)

        login_response = await self._start_remote_login(job.job_id, request, "login_only")
        if login_response.code >= 500:
            return self._task_response(
                job.job_id,
                "failed",
                "登录任务创建失败",
                code=500,
                log_file_path=str(log_path),
                reason=login_response.message,
            )

        current_job = self.job_store.get(job.job_id)
        return self._task_response(
            job.job_id,
            "login_required",
            "登录任务创建成功，需要用户登录",
            login_url=current_job.login_url if current_job else "",
            remote_session_id=current_job.remote_session_id if current_job else "",
            log_file_path=current_job.log_file_path if current_job else str(log_path),
        )

    def _schedule_publish(self, job_id: str, request: AutoPublishRequest) -> None:
        task = asyncio.create_task(self._publish_with_cookie(job_id, request))
        self._background_tasks.add(task)
        task.add_done_callback(lambda done_task, job_id=job_id: self._on_background_publish_done(job_id, done_task))

    def _on_background_publish_done(self, job_id: str, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        logger, _ = setup_job_logger(job_id, self.log_dir)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("后台发布任务已取消")
        except Exception as exc:
            logger.exception("后台发布任务异常退出: %s", exc)
            self.job_store.update(job_id, status=STATUS_FAILED, error=str(exc))

    def _task_response(
        self,
        job_id: str,
        task_status: str,
        message: str,
        code: int = 200,
        login_url: str = "",
        remote_session_id: str = "",
        live_url: str = "",
        log_file_path: str = "",
        reason: str = "",
    ) -> AutoTaskCreateResponse:
        data = {
            "job_id": job_id,
            "task_status": task_status,
            "query_url": f"/api/v1/publish/jobs/{job_id}",
            "login_url": login_url,
            "remote_session_id": remote_session_id,
            "live_url": live_url,
            "log_file_path": log_file_path,
        }
        if reason:
            data["reason"] = reason
        return AutoTaskCreateResponse(code=code, message=message, data=data)

    async def _start_remote_login(
        self,
        job_id: str,
        request: AutoPublishRequest | LoginRequest,
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

        async def on_live_url_ready(live_url: str) -> None:
            self.job_store.update(job_id, live_url=live_url)
            logger.info("发布实时查看链接已生成 live_url=%s", live_url)

        try:
            result = await self.publish_adapter.publish(
                title=request.title,
                content=request.content,
                cookie=cookie,
                request_id=job_id,
                cover_image_url=request.cover_image_url,
                on_live_url_ready=on_live_url_ready,
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
        is_login_only = (job.payload or {}).get("job_type") == "login_only"
        request = LoginRequest(**job.payload) if is_login_only else AutoPublishRequest(**job.payload)
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
        if is_login_only:
            result = {
                "success": True,
                "login_only": True,
                "cookie_ready": True,
                "cookie_count": len(cookies),
            }
            self.job_store.update(job_id, status=STATUS_SUCCEEDED, result=result, error="")
            logger.info("仅登录任务 Cookie 保存完成，不执行发文")
            return AutoPublishResponse(
                code=200,
                job_id=job_id,
                status=STATUS_SUCCEEDED,
                message="登录 Cookie 保存成功",
                log_file_path=job.log_file_path,
                result=result,
            )

        return await self._publish_with_cookie(job_id, request)

    async def save_remote_cookie(self, job_id: str) -> AutoPublishResponse:
        job = self.job_store.get(job_id)
        if job is None:
            return AutoPublishResponse(code=404, job_id=job_id, status=STATUS_FAILED, message="job not found")

        logger, _ = setup_job_logger(job_id, self.log_dir)
        if job.status in {STATUS_PUBLISHING, STATUS_SUCCEEDED}:
            logger.info("手动保存 Cookie 跳过：job 已经进入发布流程 status=%s", job.status)
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="cookie already saved",
                log_file_path=job.log_file_path,
            )
        if not job.remote_session_id:
            logger.warning("手动保存 Cookie 失败：job 没有关联远程登录 session")
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="job has no remote login session",
                log_file_path=job.log_file_path,
            )
        if self.remote_runner is None:
            logger.warning("手动保存 Cookie 失败：远程登录 runner 不存在")
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="remote login runner not available",
                log_file_path=job.log_file_path,
            )

        logger.info("收到手动保存 Cookie 请求 remote_session_id=%s", job.remote_session_id)
        try:
            cookies = await self.remote_runner.save_session_cookies(job.remote_session_id)
        except KeyError:
            logger.exception("手动保存 Cookie 失败：远程登录 session 不存在")
            return AutoPublishResponse(
                code=404,
                job_id=job_id,
                status=STATUS_FAILED,
                message="remote login session not found",
                log_file_path=job.log_file_path,
            )
        except Exception as exc:
            logger.exception("手动保存 Cookie 失败: %s", exc)
            self.job_store.update(job_id, status=STATUS_FAILED, error=str(exc))
            return AutoPublishResponse(
                code=500,
                job_id=job_id,
                status=STATUS_FAILED,
                message=str(exc),
                log_file_path=job.log_file_path,
            )

        current_job = self.job_store.get(job_id)
        if not cookies:
            logger.info("手动保存 Cookie 跳过：session 已完成")
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=current_job.status if current_job else job.status,
                message="cookie already saved",
                log_file_path=job.log_file_path,
            )
        logger.info("手动保存 Cookie 成功 cookie_count=%s", len(cookies))
        return AutoPublishResponse(
            code=200,
            job_id=job_id,
            status=current_job.status if current_job else job.status,
            message="Cookie 保存成功，发布任务已继续执行",
            log_file_path=current_job.log_file_path if current_job else job.log_file_path,
            result={
                "cookie_count": len(cookies),
                "query_url": f"/api/v1/publish/jobs/{job_id}",
            },
        )

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
            "cookie无效",
            "cookie 失效",
            "cookie失效",
            "login",
            "auth/page/login",
        )
        return any(marker.lower() in text for marker in markers)
