"""
搜狐号平台配置
"""

from dataclasses import dataclass, field
from .base import PlatformConfig


@dataclass
class SohuPlatform(PlatformConfig):
    name: str = "sohu"
    home_url: str = "https://mp.sohu.com"
    publish_url: str = "https://mp.sohu.com"
    auth_domains: list = field(default_factory=lambda: [".sohu.com", "mp.sohu.com", "www.sohu.com"])
    users_yaml: str = "sohu_users.yaml"
    account_cookies: list = field(default_factory=list)

    def get_agent_prompt(self, title: str, content: str,
                         cover_instruction: str,
                         body_image_instruction: str,
                         is_markdown: bool,
                         rich_html: str = None) -> str:
        if is_markdown and rich_html:
            return self._rich_html_ready_prompt(title, content, cover_instruction, body_image_instruction, rich_html)
        elif is_markdown:
            return self._markdown_prompt(title, content, cover_instruction, body_image_instruction)
        else:
            return self._plain_text_prompt(title, content, cover_instruction, body_image_instruction)

    def _rich_html_ready_prompt(self, title, content, cover_instruction, body_image_instruction, rich_html):
        """当 Markdown 已通过 Python 转换为 HTML 时使用。Agent 通过 execute_script 直接注入 HTML 到编辑器"""
        return f"""请按顺序完成以下步骤来发布一篇文章到搜狐号：

【背景】Markdown 内容已通过 Python 转换为 HTML 富文本。Agent 需要通过浏览器执行 JavaScript 将其注入到编辑器。

1. 导航到搜狐号后台: {self.home_url}
2. 确认已登录后台。
3. 找到发布/写文章的入口，进入文章编辑页面。
4. 找到标题输入框，输入标题: "{title}"
5. 找到正文编辑区域。
6. **注入富文本 HTML**：
   使用 evaluate 执行 JavaScript：
   ```javascript
   const editor = document.querySelector('[contenteditable="true"]');
   if (editor) {{
     editor.focus();
     editor.innerHTML = `{rich_html}`;
     editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
   }}
   ```
7. 等待编辑器渲染，确认内容已正确显示。
8. {cover_instruction.strip()}
9. 找到发布按钮，点击发布。
10. 调用 done 动作时，返回 JSON: {{"success": true/false, "account_name": "账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}
"""

    def _markdown_prompt(self, title, content, cover_instruction, body_image_instruction):
        return f"""请按顺序完成以下步骤来发布一篇文章到搜狐号：

【第一阶段：Markdown 转富文本】
1. 首先导航到: https://markdowntorichtext.com/zh/
2. 等待页面加载完成。
3. 找到页面的 Markdown 输入区域（通常是一个大的 textarea 或 contenteditable 区域）。
4. 使用以下方法将 Markdown 内容粘贴到输入区域：
   - 使用 evaluate 执行 JavaScript：
     const textarea = document.querySelector('textarea') || document.querySelector('[contenteditable]');
     if (textarea) {{ textarea.value = `{content}`.slice(0, 50000); textarea.dispatchEvent(new Event('input', {{ bubbles: true }})); }}
5. 等待页面渲染预览。
6. 找到页面上的"复制"按钮，点击它。
7. 确认剪贴板中已有富文本内容。

【第二阶段：发布到搜狐号】
8. 导航到搜狐号后台: {self.home_url}
9. 确认已登录后台。
10. 找到发布/写文章的入口，进入文章编辑页面。
11. 找到标题输入框，输入标题: "{title}"
12. 找到正文编辑区域，粘贴富文本内容。{body_image_instruction}
13. 等待编辑器渲染，确认内容已正确显示。
14. {cover_instruction.strip()}
15. 找到发布按钮，点击发布。
16. 调用 done 动作时，返回 JSON: {{"success": true/false, "account_name": "账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}
"""

    def _plain_text_prompt(self, title, content, cover_instruction, body_image_instruction):
        return f"""请按顺序完成以下步骤来发布一篇文章到搜狐号：

1. 首先导航到: {self.home_url}
2. 确认已登录后台。
3. 找到发布/写文章的入口，进入文章编辑页面。
4. 找到标题输入框，输入标题: "{title}"
5. 找到正文编辑区域，输入以下内容:{body_image_instruction}

<content>
{content}
</content>{cover_instruction}
7. 找到发布按钮，点击发布。
8. 调用 done 动作时，返回 JSON: {{"success": true/false, "account_name": "账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}
"""
