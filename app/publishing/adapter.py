from collections.abc import Callable
from typing import Any


class PublishServiceAdapter:
    """Adapter for the platform-specific publish service."""

    def __init__(
        self,
        publisher_factory: Callable[[], Any] | None = None,
        publisher_factories: dict[str, Callable[[], Any]] | None = None,
    ):
        self._publisher_factory = publisher_factory
        self._publisher_factories = publisher_factories

    def _publisher(self, platform: str, user_id: str = ""):
        platform = (platform or "toutiao").strip()
        if self._publisher_factories is not None:
            if platform not in self._publisher_factories:
                raise ValueError(f"unsupported platform: {platform}")
            return self._publisher_factories[platform]()
        if self._publisher_factory:
            return self._publisher_factory()
        if platform == "sohu":
            from app.publishing.sohu_service import AutoSohuPublishService

            return AutoSohuPublishService(user_id=user_id)
        if platform != "toutiao":
            raise ValueError(f"unsupported platform: {platform}")
        from app.publishing.toutiao_service import AutoToutiaoPublishService

        return AutoToutiaoPublishService()

    async def publish(
        self,
        platform: str,
        title: str,
        content: str,
        cookie: str,
        request_id: str,
        user_id: str = "",
        cover_image_url: str | None = None,
        cover_image_path: str | None = None,
        on_live_url_ready=None,
    ) -> dict:
        return await self._publisher(platform, user_id=user_id).publish(
            title=title,
            content=content,
            cookie=cookie,
            request_id=request_id,
            cover_image_path=cover_image_path,
            cover_image_url=cover_image_url,
            on_live_url_ready=on_live_url_ready,
        )
