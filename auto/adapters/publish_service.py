from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"


class PublishServiceAdapter:
    """Adapter over the existing publish service without modifying it."""

    def __init__(self, publisher_factory: Callable[[], Any] | None = None):
        self._publisher_factory = publisher_factory

    def _ensure_import_paths(self) -> None:
        for path in (PROJECT_ROOT, SRC_DIR):
            path_text = str(path)
            if path.exists() and path_text not in sys.path:
                sys.path.insert(0, path_text)

    def _publisher(self):
        self._ensure_import_paths()
        if self._publisher_factory:
            return self._publisher_factory()
        from auto.adapters.toutiao_publish_service import AutoToutiaoPublishService

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
        self._ensure_import_paths()
        return await self._publisher().publish(
            title=title,
            content=content,
            cookie=cookie,
            request_id=request_id,
            cover_image_path=cover_image_path,
            cover_image_url=cover_image_url,
        )
