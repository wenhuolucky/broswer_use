from __future__ import annotations

import asyncio

from app.publishing.adapter import PublishServiceAdapter
from app.channels.store import ChannelStore
from app.jobs.store import JobStore
from app.core.request_logging import setup_job_logger
from app.domain.job import (
    AutoPublishRequest,
    AutoPublishResponse,
    AutoTaskCreateResponse,
    LoginRequest,
    STATUS_CANCELLED,
    STATUS_CHECKING_COOKIE,
    STATUS_COOKIE_READY,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_QUEUED,
    STATUS_STARTING_REMOTE_LOGIN,
    STATUS_SUCCEEDED,
    STATUS_WAITING_COOKIE,
)
from app.platforms import registry
from app.remote.login import RemoteLoginRunner
from app.core.config import LOG_DIR


class PublishAgent:
    """Coordinates channel lookup, remote login, and publishing.

    The caller deals only in ``channel_id`` (a service-issued handle bound 1:1 to
    a platform account). Publishing resolves the platform from the channel; when a
    channel's cookie is missing/expired it transparently re-logs-in that same
    channel and resumes.
    """

    def __init__(
        self,
        job_store: JobStore | None = None,
        channel_store: ChannelStore | None = None,
        publish_adapter: PublishServiceAdapter | None = None,
        remote_runner: RemoteLoginRunner | None = None,
        log_dir=None,
    ):
        self.job_store = job_store or JobStore()
        self.channel_store = channel_store or ChannelStore()
        self.publish_adapter = publish_adapter or PublishServiceAdapter()
        self.log_dir = log_dir or LOG_DIR
        # 提前建好 runner：预热池需要在 app 启动时就能拿到它（不能再等首个登录请求
        # 才惰性创建）。logger 按 session_id 反查 job，warm 会话（无 job）落到通用 sink。
        self.remote_runner = remote_runner or RemoteLoginRunner(
            on_cookie_ready=self._resume_for_remote_session,
            logger_factory=self._remote_logger,
        )
        self._background_tasks: set[asyncio.Task] = set()
        # Index background publish tasks by job_id so cancel_job can reach them.
        self._tasks_by_job: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    async def submit(self, request: AutoPublishRequest) -> AutoTaskCreateResponse:
        channel = self.channel_store.get(request.channel_id)
        if channel is None:
            return self._task_response(
                "",
                "渠道不存在，请先登录",
                code=404,
                channel_id=request.channel_id,
                error_detail="channel not found",
                status=STATUS_FAILED,
            )

        payload = request.model_dump()
        payload["platform"] = channel.platform
        job = self.job_store.create(payload)
        logger, log_path = setup_job_logger(job.job_id, self.log_dir)
        self.job_store.update(job.job_id, log_file_path=str(log_path))
        logger.info(
            "收到自动发文请求 channel_id=%s platform=%s title=%s content_length=%s",
            request.channel_id,
            channel.platform,
            request.title,
            len(request.content),
        )
        self.job_store.update(job.job_id, status=STATUS_CHECKING_COOKIE)

        if self.channel_store.has_valid_cookie(request.channel_id):
            logger.info("Cookie 存在且有效，创建后台发布任务")
            self._schedule_publish(job.job_id)
            return self._task_response(
                job.job_id,
                "任务创建成功，发布任务正在后台执行",
                channel_id=request.channel_id,
            )

        logger.info("Cookie 不存在或无效，准备启动远程登录")
        login_response = await self._start_remote_login(job.job_id, "Cookie 不存在或无效")
        if login_response.code >= 500:
            return self._task_response(
                job.job_id,
                "任务创建失败",
                code=500,
                channel_id=request.channel_id,
                error_detail=login_response.message,
            )

        current_job = self.job_store.get(job.job_id)
        return self._task_response(
            job.job_id,
            "任务创建成功，需要用户登录",
            channel_id=request.channel_id,
            live_url=(current_job.live_url or current_job.login_url) if current_job else "",
        )

    # ------------------------------------------------------------------
    # Login-only / re-login
    # ------------------------------------------------------------------
    async def start_login_only(self, request: LoginRequest) -> AutoTaskCreateResponse:
        # 新登录：先签发一个 pending 渠道，登录成功后再绑定到真实平台账号。
        channel = self.channel_store.create(request.platform)
        payload = {
            "platform": request.platform,
            "channel_id": channel.channel_id,
            "job_type": "login_only",
        }
        job = self.job_store.create(payload)
        logger, log_path = setup_job_logger(job.job_id, self.log_dir)
        self.job_store.update(job.job_id, log_file_path=str(log_path))
        logger.info(
            "收到仅登录请求 platform=%s channel_id=%s", request.platform, channel.channel_id
        )

        login_response = await self._start_remote_login(job.job_id, "login_only")
        if login_response.code >= 500:
            return self._task_response(
                job.job_id,
                "登录任务创建失败",
                code=500,
                channel_id=channel.channel_id,
                error_detail=login_response.message,
            )

        current_job = self.job_store.get(job.job_id)
        return self._task_response(
            job.job_id,
            "登录任务创建成功，需要用户登录",
            channel_id=channel.channel_id,
            live_url=(current_job.live_url or current_job.login_url) if current_job else "",
        )

    # ------------------------------------------------------------------
    # Background publish task plumbing
    # ------------------------------------------------------------------
    def _schedule_publish(self, job_id: str) -> None:
        task = asyncio.create_task(self._publish_with_cookie(job_id))
        self._background_tasks.add(task)
        self._tasks_by_job[job_id] = task
        task.add_done_callback(lambda done_task, job_id=job_id: self._on_background_publish_done(job_id, done_task))

    def _on_background_publish_done(self, job_id: str, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if self._tasks_by_job.get(job_id) is task:
            self._tasks_by_job.pop(job_id, None)
        # viewer 随发文任务结束已被拆（kernel finally），摘除反代映射避免悬挂。
        from app.api.publish_viewer_proxy import unregister_viewer

        unregister_viewer(job_id)
        logger, _ = setup_job_logger(job_id, self.log_dir)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("后台发布任务已取消")
        except Exception as exc:
            logger.exception("后台发布任务异常退出: %s", exc)
            self.job_store.update(job_id, status=STATUS_FAILED, error=str(exc))

    def close_stale_running_jobs_after_restart(self) -> int:
        stale_statuses = {
            STATUS_QUEUED,
            STATUS_CHECKING_COOKIE,
            STATUS_COOKIE_READY,
            STATUS_STARTING_REMOTE_LOGIN,
            STATUS_WAITING_COOKIE,
            STATUS_PUBLISHING,
        }
        stale_jobs = self.job_store.list_by_statuses(stale_statuses)
        # 登录会话与发布任务底层共用 Job 存储、靠 job.type 区分，对外是两套解耦资源；
        # 重启清理的失败文案也要按类型给，避免登录会话拿到发布口径的提示。
        reason_by_type = {
            "login": "服务已重启，运行中的浏览器会话和实时查看页面已失效，请重新创建登录会话",
            "publish": "服务已重启，运行中的浏览器会话和实时查看页面已失效，请重新创建发布任务",
        }
        for job in stale_jobs:
            reason = reason_by_type.get(job.type, reason_by_type["publish"])
            logger, _ = setup_job_logger(job.job_id, self.log_dir)
            logger.warning(
                "服务启动时关闭遗留任务 type=%s status=%s live_url=%s remote_session_id=%s",
                job.type,
                job.status,
                job.live_url,
                job.remote_session_id,
            )
            self.job_store.update(
                job.job_id,
                status=STATUS_FAILED,
                error=reason,
                live_url="",
                login_url="",
                remote_session_id="",
            )
        return len(stale_jobs)

    def _task_response(
        self,
        job_id: str,
        message: str,
        code: int = 200,
        channel_id: str = "",
        live_url: str = "",
        error_detail: str = "",
        status: str = "",
    ) -> AutoTaskCreateResponse:
        # 只回传调用方真正用得到的字段：job_id / channel_id / status、登录时的 live_url、
        # 失败时的 error_detail。status 为原始内部状态：有 job_id 时以 store 最新值为准，
        # 否则用显式传入的兜底（如渠道不存在、尚无 job 时）。
        if not status and job_id:
            current = self.job_store.get(job_id)
            status = current.status if current else ""
        data = {
            "job_id": job_id,
            "channel_id": channel_id,
            "status": status,
            "live_url": live_url,
        }
        if error_detail:
            data["error_detail"] = error_detail
        return AutoTaskCreateResponse(code=code, message=message, data=data)

    async def _start_remote_login(
        self,
        job_id: str,
        reason: str,
        mark_cookie_refresh_attempted: bool = False,
    ) -> AutoPublishResponse:
        logger, _ = setup_job_logger(job_id, self.log_dir)
        job = self.job_store.get(job_id)
        log_file_path = job.log_file_path if job else ""
        payload = dict(job.payload or {}) if job else {}
        platform = str(payload.get("platform", "") or "")
        channel_id = str(payload.get("channel_id", "") or "")
        self.job_store.update(job_id, status=STATUS_STARTING_REMOTE_LOGIN)
        if mark_cookie_refresh_attempted:
            payload["cookie_refresh_attempted"] = True
            self.job_store.update(job_id, payload=payload)
        try:
            session = await self.remote_runner.start(platform, channel_id)
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
            live_url=session.login_url,
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
            message="请打开 live_url 完成登录，登录成功后系统可继续发文",
            live_url=session.login_url,
            log_file_path=log_file_path,
        )

    async def _publish_with_cookie(self, job_id: str) -> AutoPublishResponse:
        logger, _ = setup_job_logger(job_id, self.log_dir)
        job = self.job_store.get(job_id)
        log_file_path = job.log_file_path if job else ""
        payload = dict(job.payload or {}) if job else {}
        platform = str(payload.get("platform", "") or "")
        channel_id = str(payload.get("channel_id", "") or "")
        self.job_store.update(job_id, status=STATUS_PUBLISHING, live_url="")
        logger.info("开始调用自动化发文服务")
        cookie = self.channel_store.cookie_text(channel_id)

        channel = self.channel_store.get(channel_id)
        config = registry.config_for(platform)
        article_account_id = (
            config.article_url_account_id(channel.metadata) if (config and channel) else ""
        )

        async def on_live_url_ready(viewer_port: int) -> None:
            # 登记 job_id→viewer_port，并构造经主服务反代的对外 live_url——与登录 live_url
            # 同一套 base（REMOTE_PUBLIC_BASE_URL 或本机主服务端口），故同样哪都能打开。
            from app.api.publish_viewer_proxy import register_viewer
            from app.remote.login import _public_base_url

            register_viewer(job_id, viewer_port)
            live_url = f"{_public_base_url()}/publish-viewer/{job_id}/"
            self.job_store.update(job_id, live_url=live_url)
            logger.info("发布实时查看链接已生成 live_url=%s", live_url)

        try:
            result = await self.publish_adapter.publish(
                platform=platform,
                title=str(payload.get("title", "") or ""),
                content=str(payload.get("content", "") or ""),
                cookie=cookie,
                request_id=job_id,
                article_account_id=article_account_id,
                cover_image_url=payload.get("cover_image_url"),
                on_live_url_ready=on_live_url_ready,
            )
        except Exception as exc:
            logger.exception("自动化发文服务异常: %s", exc)
            if self._looks_like_login_required({"failure_reason": str(exc)}) and not self._cookie_refresh_attempted(job):
                logger.info("自动化发文异常疑似 Cookie 失效，切换远程登录")
                return await self._start_remote_login(
                    job_id,
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

    # ------------------------------------------------------------------
    # Cookie capture → channel binding → resume
    # ------------------------------------------------------------------
    async def resume_after_cookie(self, job_id: str, cookies: list[dict]) -> AutoPublishResponse:
        job = self.job_store.get(job_id)
        if job is None:
            return AutoPublishResponse(code=404, job_id=job_id, status=STATUS_FAILED, message="job not found")

        logger, _ = setup_job_logger(job_id, self.log_dir)
        payload = dict(job.payload or {})
        platform = str(payload.get("platform", "") or "")
        channel_id = str(payload.get("channel_id", "") or "")
        is_login_only = payload.get("job_type") == "login_only"
        logger.info("收到远程登录 Cookie 回调 cookie_count=%s", len(cookies))

        config = registry.config_for(platform)
        native_key = config.extract_native_key(cookies) if config else ""
        account_name = config.extract_account_name(cookies) if config else ""

        try:
            channel = self.channel_store.bind(
                channel_id,
                cookie={"cookies": cookies, "origins": []},
                native_key=native_key,
                account_name=account_name,
                metadata={},
            )
        except KeyError:
            logger.error("绑定 Cookie 失败：渠道不存在 channel_id=%s", channel_id)
            self.job_store.update(job_id, status=STATUS_FAILED, error="channel not found")
            return AutoPublishResponse(
                code=404, job_id=job_id, status=STATUS_FAILED, message="channel not found",
                log_file_path=job.log_file_path,
            )

        # 去重 A 可能把本次登录并到了已有渠道：把 job 指向规范 channel_id。
        canonical_id = channel.channel_id
        if canonical_id != channel_id:
            payload["channel_id"] = canonical_id
            channel_id = canonical_id
            self.job_store.update(job_id, payload=payload)

        if not self.channel_store.has_valid_cookie(channel_id):
            logger.error("远程 Cookie 无效")
            self.job_store.update(job_id, status=STATUS_FAILED, error="remote cookie invalid")
            return AutoPublishResponse(
                code=400,
                job_id=job_id,
                status=STATUS_FAILED,
                message="remote cookie invalid",
                log_file_path=job.log_file_path,
            )

        logger.info("远程 Cookie 已保存且验证通过 channel_id=%s", channel_id)
        if is_login_only:
            result = {
                "success": True,
                "login_only": True,
                "cookie_ready": True,
                "cookie_count": len(cookies),
                "channel_id": channel_id,
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

        self.job_store.update(job_id, status=STATUS_PUBLISHING, live_url="", error="")
        self._schedule_publish(job_id)
        return AutoPublishResponse(
            code=200,
            job_id=job_id,
            status=STATUS_PUBLISHING,
            message="Cookie 保存成功，发文任务已继续后台执行",
            log_file_path=job.log_file_path,
            result={"cookie_count": len(cookies), "channel_id": channel_id},
        )

    async def save_remote_cookie(self, job_id: str) -> AutoPublishResponse:
        job = self.job_store.get(job_id)
        if job is None:
            return AutoPublishResponse(code=404, job_id=job_id, status=STATUS_FAILED, message="job not found")

        logger, _ = setup_job_logger(job_id, self.log_dir)
        if job.status in {STATUS_PUBLISHING, STATUS_SUCCEEDED}:
            logger.info("Manual cookie save skipped because job already moved on status=%s", job.status)
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="cookie already saved",
                log_file_path=job.log_file_path,
            )
        if not job.remote_session_id:
            logger.warning("Manual cookie save failed because job has no remote session")
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="job has no remote login session",
                log_file_path=job.log_file_path,
            )
        if self.remote_runner is None:
            logger.warning("Manual cookie save failed because remote runner is unavailable")
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="remote login runner not available",
                log_file_path=job.log_file_path,
            )

        logger.info("Received manual cookie save request remote_session_id=%s", job.remote_session_id)
        try:
            cookies = await self.remote_runner.save_session_cookies(job.remote_session_id, notify=False)
        except KeyError:
            logger.exception("Manual cookie save failed because remote session does not exist")
            return AutoPublishResponse(
                code=404,
                job_id=job_id,
                status=STATUS_FAILED,
                message="remote login session not found",
                log_file_path=job.log_file_path,
            )
        except Exception as exc:
            logger.exception("Manual cookie save failed: %s", exc)
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
            logger.info("Manual cookie save skipped because session is already completed")
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=current_job.status if current_job else job.status,
                message="cookie already saved",
                log_file_path=job.log_file_path,
            )

        resume_response = await self.resume_after_cookie(job_id, cookies)
        logger.info("Manual cookie save succeeded cookie_count=%s", len(cookies))
        return AutoPublishResponse(
            code=200,
            job_id=job_id,
            status=resume_response.status,
            message=resume_response.message,
            log_file_path=resume_response.log_file_path,
            result=resume_response.result,
        )

    async def cancel_job(self, job_id: str) -> AutoPublishResponse:
        """Cancel a running publish/login job: tear down any remote login session,
        cancel the background publish task, and mark the job cancelled."""
        job = self.job_store.get(job_id)
        if job is None:
            return AutoPublishResponse(code=404, job_id=job_id, status=STATUS_FAILED, message="job not found")

        if job.status in {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED}:
            return AutoPublishResponse(
                code=409,
                job_id=job_id,
                status=job.status,
                message="job already finished",
                log_file_path=job.log_file_path,
            )

        logger, _ = setup_job_logger(job_id, self.log_dir)
        logger.info("收到取消任务请求 status=%s remote_session_id=%s", job.status, job.remote_session_id)

        # 1. Tear down the remote login session (browser/Xvnc/display slot) if any.
        if job.remote_session_id and self.remote_runner is not None:
            try:
                await self.remote_runner.cleanup(job.remote_session_id)
            except Exception as exc:  # cleanup is best-effort
                logger.warning("取消任务时清理远程登录会话失败: %s", exc)

        # 2. Cancel the background publish task if one is running.
        task = self._tasks_by_job.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()

        # 3. Mark the job cancelled and clear transient URLs.
        self.job_store.update(
            job_id,
            status=STATUS_CANCELLED,
            error="cancelled by request",
            live_url="",
            login_url="",
            remote_session_id="",
        )
        return AutoPublishResponse(
            code=200,
            job_id=job_id,
            status=STATUS_CANCELLED,
            message="任务已取消",
            log_file_path=job.log_file_path,
        )

    # ------------------------------------------------------------------
    # Channels (cookie-bearing accounts)
    # ------------------------------------------------------------------
    def get_channel(self, channel_id: str):
        return self.channel_store.get(channel_id)

    def channel_cookie_valid(self, channel_id: str) -> bool:
        return self.channel_store.has_valid_cookie(channel_id)

    def delete_channel(self, channel_id: str) -> bool:
        """Delete a channel (its cookie + record). Returns True if it existed."""
        return self.channel_store.delete(channel_id)

    def _remote_logger(self, session_id: str):
        # 把远程登录会话的日志按 session_id 反查到对应 job 的 sink；预热会话还没绑
        # job，落到通用 "remote-login" sink。
        job = self.job_store.find_by_remote_session(session_id)
        job_id = job.job_id if job else "remote-login"
        return setup_job_logger(job_id, self.log_dir)[0]

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
