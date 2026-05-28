"""
并发发布器 - 支持多用户并发发布文章

跨用户：无限制并发，通过 asyncio.gather 实现
同用户：串行发布，通过 per-user asyncio.Lock 排队
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from user_manager import UserManager


class ConcurrentPublisher:
    """
    多用户并发发布管理器

    使用 asyncio.gather 实现跨用户并发，
    使用 per-user asyncio.Lock 实现同用户串行排队。
    """

    def __init__(self, user_manager: UserManager):
        self.user_manager = user_manager

    async def publish_for_user(
        self,
        user_id: str,
        title: str,
        content: str,
        image_paths: Optional[List[str]] = None,
        fresh_profile: bool = False,
    ) -> dict:
        """
        为指定用户发布文章。

        如果该用户正在发布中，新请求会等待完成后再执行。
        fresh_profile=True 时，每次发布使用全新的浏览器 profile。
        """
        user_config = self.user_manager.get_user(user_id)
        if not user_config:
            return {
                "success": False,
                "user_id": user_id,
                "failure_reason": f"未知用户: {user_id}",
            }

        # 获取用户锁（等待直到用户空闲）
        lock = await self.user_manager.acquire_user_slot(user_id)
        try:
            # 延迟导入避免循环依赖
            from publisher import publish_article_internal

            result = await publish_article_internal(
                title=title,
                content=content,
                image_paths=image_paths,
                auth_file=user_config.auth_file,
                user_data_dir=user_config.chrome_profile,
                cdp_port=user_config.cdp_port,
                fresh_profile=fresh_profile,
            )
            result["user_id"] = user_id
            return result
        finally:
            self.user_manager.release_user_slot(user_id)

    async def publish_batch(self, requests: List[dict]) -> List[dict]:
        """
        批量并发发布文章。

        requests: [{"user_id": str, "title": str, "content": str, "image_paths": list}, ...]
        跨用户并发执行，同用户串行排队。
        """
        tasks = []
        for req in requests:
            task = self.publish_for_user(
                user_id=req["user_id"],
                title=req["title"],
                content=req["content"],
                image_paths=req.get("image_paths"),
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append({
                    "user_id": requests[i].get("user_id", "unknown"),
                    "success": False,
                    "failure_reason": str(result),
                })
            else:
                processed.append(result)
        return processed


async def publish_for_user(
    user_id: str,
    title: str,
    content: str,
    image_paths: Optional[List[str]] = None,
    fresh_profile: bool = False,
) -> dict:
    """
    便捷函数：通过全局 UserManager 为用户发布文章。
    """
    user_mgr = UserManager.get_instance()
    publisher = ConcurrentPublisher(user_mgr)
    return await publisher.publish_for_user(user_id, title, content, image_paths, fresh_profile)


async def publish_batch(requests: List[dict]) -> List[dict]:
    """
    便捷函数：批量并发发布。
    """
    user_mgr = UserManager.get_instance()
    publisher = ConcurrentPublisher(user_mgr)
    return await publisher.publish_batch(requests)