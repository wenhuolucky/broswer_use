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

    def _publisher(self, platform: str, article_account_id: str = ""):
        platform = (platform or "toutiao").strip()
        # Test/override seams take precedence over the real registry.
        if self._publisher_factories is not None:
            if platform not in self._publisher_factories:
                raise ValueError(f"unsupported platform: {platform}")
            return self._publisher_factories[platform]()
        if self._publisher_factory:
            return self._publisher_factory()
        from app.platforms import registry

        return registry.kernel_for(platform, article_account_id=article_account_id)

    async def publish(
        self,
        platform: str,
        title: str,
        content: str,
        cookie: str,
        request_id: str,
        article_account_id: str = "",
        cover_image_url: str | None = None,
        cover_image_path: str | None = None,
        on_live_url_ready=None,
    ) -> dict:
        return await self._publisher(platform, article_account_id=article_account_id).publish(
            title=title,
            content=content,
            cookie=cookie,
            request_id=request_id,
            cover_image_path=cover_image_path,
            cover_image_url=cover_image_url,
            on_live_url_ready=on_live_url_ready,
        )
