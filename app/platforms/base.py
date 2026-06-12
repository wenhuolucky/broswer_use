"""
Platform base configuration and common interfaces.
"""

from dataclasses import dataclass, field


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
