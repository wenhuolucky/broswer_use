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
        return f"""请按顺序完成以下步骤，在头条号发布一篇文章。

背景：
- 正文已经由系统从 Markdown 转换为富文本 HTML。
- 正文中的图片只允许来自 HTML 中的网络 URL。
- 本次正文导入只允许一条路径：先写入浏览器剪贴板，再执行 Ctrl+V 粘贴。
- 禁止通过 `editor.innerHTML`、DOM 注入、直接替换节点、直接拼接 HTML 片段等方式写入正文。
- 如果剪贴板写入失败，或者 Ctrl+V 后正文完全没有粘贴进去，可以返回失败。
- 如果富文本结构不完全理想，例如段落、列表、加粗、换行没有完全按预期渲染，不要长时间反复检查；记录现象后仍然继续执行发布。
- 重点确认正文已经进入编辑器，标题正确，图片尽可能展示，然后继续发布。

步骤：
1. 打开头条号后台：{self.home_url}
2. 确认当前账号已登录；如果未登录，停止并返回失败。
3. 读取当前登录账号显示名，记为 account_name。
4. 打开文章发布页：{self.publish_url}
5. 等待页面加载完成，输入标题："{title}"
6. 找到正文富文本编辑器，点击使其获得焦点。
7. 使用 evaluate 执行下面的 JavaScript，把完整 HTML 写入浏览器剪贴板：
```javascript
async (htmlContent) => {{
  const stripTags = (s) => s.replace(/<[^>]+>/g, "");
  try {{
    if (navigator.clipboard && window.ClipboardItem) {{
      const item = new ClipboardItem({{
        "text/html": new Blob([htmlContent], {{ type: "text/html" }}),
        "text/plain": new Blob([stripTags(htmlContent)], {{ type: "text/plain" }})
      }});
      await navigator.clipboard.write([item]);
      return {{ ok: true, method: "ClipboardItem" }};
    }}
  }} catch (error) {{}}
  const textarea = document.createElement("textarea");
  textarea.value = htmlContent;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  return {{ ok: !!ok, method: "execCommand" }};
}}
```
8. 保持编辑器焦点，使用 `send_keys("Control+v")` 进行粘贴，也就是执行 Ctrl+V。
9. 只做一次快速检查：
   - 标题正确
   - 正文已经进入编辑器，不是空白
   - 图片如果能显示则保留；如果部分图片未显示，也继续发布
   - 如果段落、列表、加粗、换行等结构不完全理想，不要反复重试
10. 如果第 7 步失败，返回：`failure_reason="clipboard_html_write_failed"`。
11. 如果第 8 步后正文完全没有进入编辑器，返回：`failure_reason="richtext_paste_failed"`。
12. 绝对不要使用 `innerHTML` 或任何 DOM 注入方式伪造粘贴成功。
13. {cover_instruction.strip()}
14. 检查分类等必填项。
15. 点击“预览并发布”，再点击“确认发布”。
16. 点击“确认发布”后立即调用 done，并返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": ""}}

完整 HTML 长度：{html_length} 字符

========== HTML BEGIN ==========
{rich_html}
========== HTML END ==========
"""

    def _markdown_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        content_preview = content[:500] if len(content) > 500 else content
        content_length = len(content)
        return f"""请按顺序完成以下步骤，在头条号发布一篇文章。

当前正文是 Markdown。优先使用系统已准备好的 HTML 富文本流程；如果运行时没有拿到富文本 HTML，再把正文作为普通文本处理。

1. 打开头条号后台：{self.home_url}
2. 确认当前账号已登录，并读取账号显示名记为 account_name。
3. 打开文章发布页：{self.publish_url}
4. 输入标题："{title}"
5. 找到正文编辑器，粘贴或输入正文内容。
6. {cover_instruction.strip()}
7. 完成分类等必填项。
8. 点击“预览并发布”，再点击“确认发布”。
9. 调用 done，返回合法 JSON 字符串：
   {{"success": true, "account_name": "步骤2读到的账号名", "article_url": "", "failure_reason": ""}}

正文长度：{content_length} 字符
正文预览：{content_preview}
"""

    def _plain_text_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        return f"""你在头条号后台操作。请按顺序发布文章：

1. 打开 {self.home_url}
2. 确认已登录，并读取账号显示名记为 account_name。
3. 打开发布页：{self.publish_url}
4. 输入标题："{title}"
5. 在正文编辑器输入以下内容：
<content>
{content}
</content>
6. {cover_instruction.strip()}
7. 完成分类等必填项。
8. 点击“预览并发布”，再点击“确认发布”。
9. 调用 done，返回合法 JSON 字符串：
   {{"success": true, "account_name": "步骤2读到的账号名", "article_url": "", "failure_reason": ""}}
"""
