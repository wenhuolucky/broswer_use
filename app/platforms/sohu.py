"""Sohu platform configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .base import PlatformConfig


@dataclass
class SohuPlatform(PlatformConfig):
    name: str = "sohu"
    home_url: str = "https://mp.sohu.com"
    publish_url: str = "https://mp.sohu.com"
    auth_domains: list = field(default_factory=lambda: [".sohu.com", "mp.sohu.com", "www.sohu.com"])
    account_cookies: list = field(default_factory=lambda: ["SUV", "ppinf", "ppmdig"])

    def account_id_for_user(self, user_id: str) -> str:
        user_id = str(user_id or "").strip()
        mapping = self._parse_account_id_map(os.getenv("SOHU_ACCOUNT_ID_MAP", ""))
        if user_id and user_id in mapping:
            return mapping[user_id]
        return os.getenv("SOHU_ACCOUNT_ID", "").strip()

    @staticmethod
    def _parse_account_id_map(raw_mapping: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in (raw_mapping or "").split(","):
            if ":" not in item:
                continue
            user_id, account_id = item.split(":", 1)
            user_id = user_id.strip()
            account_id = account_id.strip()
            if user_id and account_id:
                result[user_id] = account_id
        return result

    def get_agent_prompt(
        self,
        title: str,
        content: str,
        cover_instruction: str,
        body_image_instruction: str,
        is_markdown: bool,
        rich_html: str = None,
    ) -> str:
        body = rich_html if is_markdown and rich_html else content
        body_mode = "富文本 HTML" if is_markdown and rich_html else "普通文本"
        return f"""请在搜狐号后台发布一篇文章。

关键规则：
- 平台是搜狐号，不是今日头条。
- 外部接口要求成功结果必须包含 article_url。
- 搜狐号发布后如果页面显示提交审核、审核中或发布成功，并且能拿到文章 URL，也算发布成功。
- 必须获取真实 URL，不要编造 URL。
- 可接受的 URL 包括：
  1. https://mp.sohu.com/h5/v2/newsPreview?id=...&type=article
  2. https://m.sohu.com/a/...
- 如果只看到提交审核成功但没有拿到 URL，必须返回 success=false。

执行步骤：
1. 打开搜狐号后台：{self.home_url}
2. 确认当前账号已登录；如果未登录，返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 进入文章发布入口。
5. 填写标题：{title}
6. 填写正文，当前正文格式为：{body_mode}。
7. 如果页面需要点击“下一步”，点击进入发布设置页。
8. 设置封面、摘要、文章属性、分类等必填项。摘要可以从正文前 80 到 120 字生成。
9. 点击发布。
10. 发布后确认出现提交审核、审核中或发布成功信号。
11. 获取文章预览链接或移动端文章链接。
12. 拿到 URL 后，调用 done 返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读取到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
13. 如果没有拿到 URL，调用 done 返回：
    {{"success": false, "account_name": "步骤3读取到的账号名", "article_url": "", "failure_reason": "搜狐号提交审核成功，但未获取到文章 URL"}}

封面要求：
{cover_instruction.strip()}

正文内容：
<content>
{body}
</content>
"""
