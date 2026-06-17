"""Toutiao platform configuration."""

from dataclasses import dataclass, field

from app.platforms.base import PlatformConfig


@dataclass
class ToutiaoPlatform(PlatformConfig):
    name: str = "toutiao"
    home_url: str = "https://mp.toutiao.com"
    publish_url: str = "https://mp.toutiao.com/profile_v4/graphic/publish"
    auth_domains: list = field(default_factory=lambda: [".toutiao.com", "mp.toutiao.com", "www.toutiao.com"])
    account_cookies: list = field(default_factory=lambda: ["uid_tt", "uid_tt_ss"])
    # 头条登录态的标志性 cookie；extract_native_key 默认取首个（uid_tt）做账号去重键。
    login_cookie_names: list = field(default_factory=lambda: [
        "uid_tt", "uid_tt_ss", "sessionid", "sessionid_ss",
        "sid_tt", "sid_guard", "sid_ucp_v1", "ssid_ucp_v1",
    ])

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
            return self._with_url_tool_rule(self._rich_html_ready_prompt(title, cover_instruction, rich_html), title)
        if is_markdown:
            return self._with_url_tool_rule(self._markdown_prompt(title, content, cover_instruction), title)
        return self._with_url_tool_rule(self._plain_text_prompt(title, content, cover_instruction), title)

    def _with_url_tool_rule(self, prompt: str, title: str) -> str:
        return prompt + f"""

发布后 URL 获取硬性规则：
- 点击“确认发布”后，不要直接调用 done。
- 必须调用工具 `get_published_article_url`，参数 title="{title}"。
- 该工具会在内部最多查询 3 次作品列表。
- 如果工具返回 found=true，必须用工具返回的 article_url 调用 done，并返回 success=true。
- found=true 时 article_url 不允许为空，必须原样填写工具返回的 article_url。
- 如果工具返回 found=false，必须用工具返回的 reason 调用 done，并返回 success=false。
- 不要在工具返回 found=false 后继续反复查询。
"""

    def _agent_recovery_rules(self) -> str:
        return """Agent 执行规则：
- 你是自主 Agent，不是固定 workflow。遇到轻微、可恢复的问题时，可以自己判断并恢复，例如按钮暂时不可点、弹窗遮挡、页面加载慢、输入框未聚焦、分类未选择、预览弹窗未出现等。
- 同一个可恢复问题最多尝试 2 次。第 2 次仍然失败时，停止并调用 done 返回失败 JSON，不要继续循环。
- 遇到不可恢复问题时不要重试，立即调用 done 返回失败 JSON。不可恢复问题包括：账号被禁言、账号异常、账号无发布权限、登录失效、需要重新登录、验证码/风控验证、内容违规/审核拦截、平台明确提示禁止发布、网络长时间不可用。
- 如果页面异常回到首页、登录页、发布页初始状态，且没有明确的“发布成功/提交成功/文章已发布”证据，不要认为已经发布成功；先尝试恢复到当前步骤，最多 2 次，仍无法确认则返回失败。
- 不要因为已经点击“确认发布”就直接认为成功。只有看到明确成功提示、文章管理页出现同标题文章、或拿到有效文章 URL，才返回 success=true。
- 如果看到明确失败提示，failure_reason 必须尽量使用页面原文；如果没有页面原文，再用简短中文总结原因。
- done 返回必须是合法 JSON 字符串，格式为：
  {"success": false, "account_name": "已读取到则填写", "article_url": "", "failure_reason": "失败原因"}
"""

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

{self._agent_recovery_rules()}

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
16. 点击“确认发布”后不要立即假定成功；等待并确认出现明确成功证据。确认成功后调用 get_published_article_url 工具，并用工具返回的 article_url 调用 done，返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "工具返回的 article_url", "failure_reason": ""}}

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

{self._agent_recovery_rules()}

1. 打开头条号后台：{self.home_url}
2. 确认当前账号已登录，并读取账号显示名记为 account_name。
3. 打开文章发布页：{self.publish_url}
4. 输入标题："{title}"
5. 找到正文编辑器，粘贴或输入正文内容。
6. {cover_instruction.strip()}
7. 完成分类等必填项。
8. 点击“预览并发布”，再点击“确认发布”。
9. 点击“确认发布”后不要立即假定成功；等待并确认出现明确成功证据。确认成功后调用 get_published_article_url 工具，并用工具返回的 article_url 调用 done，返回合法 JSON 字符串：
   {{"success": true, "account_name": "步骤2读到的账号名", "article_url": "工具返回的 article_url", "failure_reason": ""}}

正文长度：{content_length} 字符
正文预览：{content_preview}
"""

    def _plain_text_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        return f"""你在头条号后台操作。请按顺序发布文章：

{self._agent_recovery_rules()}

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
9. 点击“确认发布”后不要立即假定成功；等待并确认出现明确成功证据。确认成功后调用 get_published_article_url 工具，并用工具返回的 article_url 调用 done，返回合法 JSON 字符串：
   {{"success": true, "account_name": "步骤2读到的账号名", "article_url": "工具返回的 article_url", "failure_reason": ""}}
"""
