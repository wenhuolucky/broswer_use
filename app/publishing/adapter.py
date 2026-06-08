from collections.abc import Callable
from typing import Any


class PublishServiceAdapter:
    """Adapter for the platform-specific publish service."""

    def __init__(self, publisher_factory: Callable[[], Any] | None = None):
        self._publisher_factory = publisher_factory

    def _publisher(self):
        if self._publisher_factory:
            return self._publisher_factory()
        from app.publishing.toutiao_service import AutoToutiaoPublishService

        return AutoToutiaoPublishService()

    async def publish(
        self,
        title: str,
        content: str,
        cookie: str,
        request_id: str,
        cover_image_url: str | None = None,
        cover_image_path: str | None = None,
    ) -> dict:
        return await self._publisher().publish(
            title=title,
            content=content,
            cookie=cookie,
            request_id=request_id,
            cover_image_path=cover_image_path,
            cover_image_url=cover_image_url,
        )
