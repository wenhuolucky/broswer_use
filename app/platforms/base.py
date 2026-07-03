"""
Platform base configuration and common interfaces.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class PlatformConfig:
    """Base platform configuration shared by concrete publisher platforms."""

    name: str
    home_url: str
    publish_url: str
    auth_domains: list
    account_cookies: list = field(default_factory=list)
    # Cookie names that prove a *logged-in* session. Empty means "any cookie on
    # an auth domain counts" (used by platforms like Sohu that don't expose a
    # single canonical session cookie name).
    login_cookie_names: list = field(default_factory=list)
    login_url_markers: list = field(default_factory=list)
    login_text_markers: list = field(default_factory=list)

    def get_agent_prompt(
        self,
        title: str,
        content: str,
        cover_instruction: str,
        body_image_instruction: str,
        is_markdown: bool,
        rich_html: str = None,
    ) -> str:
        raise NotImplementedError(f"Platform {self.name} has not implemented get_agent_prompt()")

    def validate_cookies(self, cookies: list) -> bool:
        for cookie in cookies:
            domain = cookie.get("domain", "")
            for auth_domain in self.auth_domains:
                if auth_domain in domain or domain in auth_domain:
                    return True
        return False

    def has_login_cookie(self, cookies: list) -> bool:
        """Whether ``cookies`` carry a valid logged-in session for this platform.

        Stricter than :meth:`validate_cookies`: when ``login_cookie_names`` is
        set, at least one of those named cookies must sit on an auth domain.
        This is the single source of truth for "is this channel logged in",
        used by the channel store and the remote-login completion check.
        """
        names = set(self.login_cookie_names)
        for cookie in cookies:
            if names and cookie.get("name") not in names:
                continue
            domain = str(cookie.get("domain", ""))
            if any(auth_domain in domain or domain in auth_domain for auth_domain in self.auth_domains):
                return True
        return False

    def is_login_page(self, url: str, visible_text: str = "") -> bool:
        """根据平台登录入口特征判断当前页面是否要求重新登录。

        这是浏览器预检用的页面级判断，只识别登录页/登录表单，不判断发布业务异常。
        """
        normalized_url = str(url or "").lower()
        if any(str(marker).lower() in normalized_url for marker in self.login_url_markers):
            return True

        text = " ".join(str(visible_text or "").split()).lower()
        if not text:
            return False
        matched = [
            marker
            for marker in self.login_text_markers
            if str(marker).lower() in text
        ]
        if len(matched) >= 2:
            host = (urlparse(str(url or "")).hostname or "").lower()
            return any(
                host == auth_domain.lstrip(".").lower()
                or host.endswith(auth_domain.lower())
                or host.endswith(auth_domain.lstrip(".").lower())
                for auth_domain in self.auth_domains
            )
        return False

    def extract_account_name(self, cookies: list) -> str:
        for cookie in cookies:
            if cookie.get("name") in self.account_cookies:
                return cookie.get("value", "")
        return ""

    def extract_native_key(self, cookies: list) -> str:
        """A stable per-account identifier derived from the cookie, used to
        detect "the same platform account logging in again" for dedup.

        Default: the value of the first present ``account_cookies`` entry.
        Platforms override when a different cookie better identifies the account.
        Returns ``""`` when it can't be determined (caller then skips dedup).
        """
        for cookie in cookies:
            if cookie.get("name") in self.account_cookies and cookie.get("value"):
                return str(cookie.get("value"))
        return ""

    def article_url_account_id(self, metadata: dict) -> str:
        """Platform-specific account id needed to build a public article URL,
        read out of the channel's platform-specific ``metadata`` bag.

        Default is empty (most platforms don't need one); platforms like Sohu
        override this. Keeps the channel store / job store platform-agnostic.
        """
        return ""

    @staticmethod
    def strip_markdown(content: str) -> str:
        """将 Markdown 正文降级为纯文本：去掉图片/链接/标题/强调等标记符号。"""
        text = str(content or "")
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"[*_`>#-]+", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def body_probe(content: str) -> str:
        """正文存在性探针：纯文本去除所有空白后的前 30 个字符。

        发文内核与各平台 prompt 必须共用此算法，确保两边算出的探针字符串一致。
        """
        compact = "".join(PlatformConfig.strip_markdown(content).split())
        return compact[:30]

    @staticmethod
    def normalize_title_for_match(text: str) -> str:
        """Normalize platform-rendered titles for publish URL lookup.

        Management pages may insert spaces into titles, especially before a
        numeric suffix, while the requested title has no such spaces. Removing
        all whitespace keeps matching stable across Toutiao, Sohu, and shared
        article-list fallback logic.

        Additional normalization:
        - HTML entity decoding (&amp; -> &, &quot; -> ")
        - Common full-width to half-width punctuation normalization
        - Trailing punctuation tolerance
        """
        import html as html_module

        if not text:
            return ""

        # 1. HTML 实体解码（处理 &amp;、&quot; 等）
        text = html_module.unescape(str(text))

        # 2. 常见全角标点转半角（减少平台差异）
        text = text.replace("？", "?").replace("！", "!").replace("，", ",")
        text = text.replace("（", "(").replace("）", ")").replace("：", ":")

        # 3. 移除空白字符（原有逻辑）
        text = "".join(text.split())

        # 4. 移除尾部可能的多余标点（平台可能添加）
        text = text.rstrip(".,;:!?")

        return text

    @staticmethod
    def title_matches(expected: str, actual: str) -> bool:
        normalized_expected = PlatformConfig.normalize_title_for_match(expected)
        normalized_actual = PlatformConfig.normalize_title_for_match(actual)
        return bool(
            normalized_expected
            and normalized_actual
            and (
                normalized_expected == normalized_actual
                or normalized_expected in normalized_actual
                or normalized_actual in normalized_expected
            )
        )
