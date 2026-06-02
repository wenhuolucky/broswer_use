"""
平台配置基类 - 定义各平台的通用接口
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlatformConfig:
    """平台配置基类，各平台继承并实现具体值"""
    name: str                   # 平台名称："toutiao" / "sohu"
    home_url: str               # 后台首页 URL
    publish_url: str            # 发布文章页面 URL
    auth_domains: list          # Cookie 验证域名列表
    users_yaml: str             # 用户配置文件路径
    account_cookies: list = field(default_factory=list)  # 账号标识 cookie 名

    def get_agent_prompt(self, title: str, content: str,
                         cover_instruction: str,
                         body_image_instruction: str,
                         is_markdown: bool,
                         rich_html: str = None) -> str:
        """生成 Agent 任务 prompt，子类可覆盖以定制平台特定流程"""
        raise NotImplementedError(f"平台 {self.name} 未实现 get_agent_prompt()")

    def validate_cookies(self, cookies: list) -> bool:
        """检查 cookies 中是否包含该平台的登录态"""
        for c in cookies:
            domain = c.get("domain", "")
            for d in self.auth_domains:
                if d in domain or domain in d:
                    return True
        return False

    def extract_account_name(self, cookies: list) -> str:
        """从 cookies 中提取账号标识"""
        for c in cookies:
            if c.get("name") in self.account_cookies:
                return c.get("value", "")
        return ""
