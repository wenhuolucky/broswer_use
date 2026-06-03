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
    users_yaml: str
    account_cookies: list = field(default_factory=list)

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

    def extract_account_name(self, cookies: list) -> str:
        for cookie in cookies:
            if cookie.get("name") in self.account_cookies:
                return cookie.get("value", "")
        return ""
