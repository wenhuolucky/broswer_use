"""Platform registry — the single source of truth for the platforms this
service can publish to / log in.

Adding a platform is now a two-file change:
  1. create ``app/platforms/<name>/config.py`` (a ``PlatformConfig`` subclass)
     and ``app/platforms/<name>/kernel.py`` (a ``PublishService`` subclass);
  2. register it in ``_REGISTRY`` below.

No other module needs to learn about the new platform: the adapter, the
orchestrator, the job store, and the ``/platforms`` route all go through here.

Config objects are lightweight dataclasses and are instantiated eagerly. The
browser-driving kernels pull in ``browser_use``/Playwright, so they are built
lazily through factory callables — importing this module stays cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.platforms.base import PlatformConfig
from app.platforms.sohu.config import SohuPlatform
from app.platforms.toutiao.config import ToutiaoPlatform


@dataclass(frozen=True)
class PlatformSpec:
    name: str
    config: PlatformConfig
    kernel_factory: Callable[[str], Any]


def _toutiao_kernel(article_account_id: str = "") -> Any:
    from app.platforms.toutiao.kernel import AutoToutiaoPublishService

    return AutoToutiaoPublishService()


def _sohu_kernel(article_account_id: str = "") -> Any:
    from app.platforms.sohu.kernel import AutoSohuPublishService

    return AutoSohuPublishService(account_id=article_account_id)


_REGISTRY: dict[str, PlatformSpec] = {
    "toutiao": PlatformSpec("toutiao", ToutiaoPlatform(), _toutiao_kernel),
    "sohu": PlatformSpec("sohu", SohuPlatform(), _sohu_kernel),
}


def is_supported(platform: str) -> bool:
    return platform in _REGISTRY


def configs() -> dict[str, PlatformConfig]:
    """name -> config, for listing platforms and reading cookie metadata."""
    return {name: spec.config for name, spec in _REGISTRY.items()}


def config_for(platform: str) -> PlatformConfig | None:
    spec = _REGISTRY.get(platform)
    return spec.config if spec else None


def kernel_for(platform: str, user_id: str = "") -> Any:
    """Build the browser-driving publish kernel for a platform.

    Raises ``ValueError`` for an unknown platform so callers surface a clean
    error instead of silently no-op'ing.
    """
    spec = _REGISTRY.get((platform or "").strip())
    if spec is None:
        raise ValueError(f"unsupported platform: {platform}")
    return spec.kernel_factory(user_id)
