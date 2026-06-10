from __future__ import annotations

from app.platforms.sohu import SohuPlatform
from app.publishing.service import PublishService
from app.utils.urls import normalize_sohu_article_url


class AutoSohuPublishService(PublishService):
    """Sohu publishing service."""

    def __init__(self, user_id: str = ""):
        super().__init__()
        self.user_id = user_id
        self._sohu_platform = SohuPlatform()

    def _platform(self):
        return self._sohu_platform

    def _account_id(self) -> str:
        return self._sohu_platform.account_id_for_user(self.user_id)

    def _extract_article_url_from_text(self, text: str) -> str:
        import re

        urls = re.findall(r"https?://[^\s<>\"'，。；、)）\]]+", text or "")
        for url in urls:
            cleaned_url = url.rstrip(".,;:!?")
            if self._looks_like_sohu_article_url(cleaned_url):
                return normalize_sohu_article_url(cleaned_url, self._account_id())
        return ""

    def _parse_agent_outcome(self, history, tracker, logger, detected_failure: dict | None = None):
        result = super()._parse_agent_outcome(history, tracker, logger, detected_failure=detected_failure)
        result["article_url"] = normalize_sohu_article_url(str(result.get("article_url", "") or ""), self._account_id())
        if result.get("success") and not result.get("article_url"):
            result["success"] = False
            result["failure_reason"] = "搜狐号提交审核成功，但未获取到文章 URL"
        if result.get("success") and result.get("article_url") and not result.get("publish_signal"):
            result["publish_signal"] = "submitted_for_review"
        return result

    @staticmethod
    def _looks_like_sohu_article_url(url: str) -> bool:
        if not url:
            return False
        # 官方最终 URL（包含旧 m.sohu.com 和新 www.sohu.com 两种）
        if "sohu.com/a/" in url and ("m.sohu.com" in url or "www.sohu.com" in url):
            return True
        if "mp.sohu.com/h5/v2/newsPreview" in url and "id=" in url:
            return True
        # 后台管理列表的预览 URL（含 /mpfe/v4/.../news/articlepreview）
        if "mp.sohu.com" in url and "/news/articlepreview" in url and "id=" in url:
            return True
        return False
