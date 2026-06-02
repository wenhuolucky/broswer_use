"""Toutiao platform configuration."""

from dataclasses import dataclass, field

from .base import PlatformConfig


@dataclass
class ToutiaoPlatform(PlatformConfig):
    name: str = "toutiao"
    home_url: str = "https://mp.toutiao.com"
    publish_url: str = "https://mp.toutiao.com/profile_v4/graphic/publish"
    auth_domains: list = field(default_factory=lambda: [".toutiao.com", "mp.toutiao.com", "www.toutiao.com"])
    users_yaml: str = "users.yaml"
    account_cookies: list = field(default_factory=lambda: ["uid_tt", "uid_tt_ss"])

    def get_agent_prompt(
        self,
        title: str,
        content: str,
        cover_instruction: str,
        body_image_instruction: str,
        is_markdown: bool,
        rich_html: str = None,
    ) -> str:
        if is_markdown and rich_html:
            return self._rich_html_ready_prompt(title, cover_instruction, rich_html)
        if is_markdown:
            return self._markdown_prompt(title, content, cover_instruction)
        return self._plain_text_prompt(title, content, cover_instruction)

    def _rich_html_ready_prompt(self, title: str, cover_instruction: str, rich_html: str) -> str:
        html_length = len(rich_html)

        return f"""请按顺序完成以下步骤来发布一篇文章：

【背景】正文已经通过 Python 从 Markdown 转换为 HTML 富文本。
完整 HTML 已附在本文档最末尾，请把它原样传给 evaluate 中的 htmlContent 参数，然后在头条编辑器中使用 Ctrl+V 粘贴。
正文中的网络图片和链接都应来自这段 HTML，本次流程不再有单独的正文图片上传步骤。

1. 导航到头条号后台：{self.home_url}
2. 确认已经登录。如果出现登录页面，停止并提示用户重新执行登录准备流程。
   - 读取当前登录账号的显示名，记录为 account_name。
3. 导航到发布文章页面：{self.publish_url}
4. 等待页面加载完成。
5. 找到标题输入框，输入标题："{title}"
6. 找到正文富文本编辑器（contenteditable=true），点击它获取焦点。
7. 使用 evaluate 执行下面的 JavaScript，把【完整 HTML】写入浏览器剪贴板：
```javascript
async (htmlContent) => {{
  const stripTags = (s) => s.replace(/<[^>]+>/g, '');
  try {{
    if (navigator.clipboard && window.ClipboardItem) {{
      const item = new ClipboardItem({{
        'text/html': new Blob([htmlContent], {{ type: 'text/html' }}),
        'text/plain': new Blob([stripTags(htmlContent)], {{ type: 'text/plain' }})
      }});
      await navigator.clipboard.write([item]);
      return {{ ok: true, method: 'ClipboardItem' }};
    }}
  }} catch (e) {{}}
  const ta = document.createElement('textarea');
  ta.value = htmlContent;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  document.body.removeChild(ta);
  return {{ ok: !!ok, method: 'execCommand' }};
}}
```
8. 在编辑器保持聚焦的情况下，必须使用 `send_keys 'Control+v'` 粘贴。
   - 不要使用 `document.execCommand('paste')`。
   - 不要额外执行正文图片上传。
9. 等待 2-3 秒让编辑器渲染富文本，然后检查正文是否已经显示：
   - 标题、段落、加粗、斜体、列表正常
   - 链接仍然存在
   - Markdown 中的网络图片已经作为正文内容显示
10. {cover_instruction.strip()}
11. 检查分类等必填项是否缺失。
12. 点击“预览并发布”，在预览对话框中点击“确认发布”。
13. 点击“确认发布”后立即调用 done，返回 JSON：
    {{"success": true/false, "account_name": "步骤2读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}

【富文本 HTML 信息】
HTML 长度：{html_length} 字符

============================================================
【完整 HTML】（必须原样复制到 evaluate 的 htmlContent 参数）
============================================================
{rich_html}
============================================================
（【完整 HTML】结束）

重要提示：
- 不要自己改写或编造 HTML 内容。
- 粘贴动作必须依赖浏览器剪贴板 + Ctrl+V。
- 正文图片只来自 HTML 中的网络图片，不再使用本地正文图片上传。
- 封面图流程保留，但不要把封面图和正文图片流程混在一起。
"""

    def _markdown_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        content_preview = content[:500] if len(content) > 500 else content
        content_length = len(content)

        return f"""请按顺序完成以下步骤来发布一篇文章：

当前正文是 Markdown。优先使用系统已准备好的 HTML 富文本流程；如果运行时没有拿到富文本 HTML，再把正文作为普通文本处理。

1. 导航到头条号后台：{self.home_url}
2. 确认已经登录，并读取当前账号显示名为 account_name。
3. 导航到发布文章页面：{self.publish_url}
4. 等待页面加载完成。
5. 输入标题："{title}"
6. 找到正文编辑器并粘贴或输入正文。
7. {cover_instruction.strip()}
8. 完成分类等必填项。
9. 点击“预览并发布”，然后点击“确认发布”。
10. 调用 done 返回 JSON：
   {{"success": true/false, "account_name": "步骤2读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}

【正文信息】
- 长度：{content_length} 字符
- 预览：{content_preview}
"""

    def _plain_text_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        return f"""你在头条号后台操作。请按顺序完成以下步骤来发布一篇文章：

1. 打开 {self.home_url}
2. 确认已经登录，并读取当前账号显示名为 account_name。
3. 打开发布文章页面：{self.publish_url}
4. 输入标题："{title}"
5. 在正文编辑器输入以下内容：

<content>
{content}
</content>

6. {cover_instruction.strip()}
7. 完成分类等必填项。
8. 点击“预览并发布”，再点击“确认发布”。
9. 调用 done 返回 JSON：
   {{"success": true/false, "account_name": "步骤2读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}
"""
