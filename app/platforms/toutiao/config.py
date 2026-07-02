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
            return self._with_url_tool_rule(
                self._tool_rich_html_prompt(title, content, cover_instruction, rich_html),
                title,
            )
        if is_markdown:
            return self._with_url_tool_rule(self._tool_plain_text_prompt(title, content, cover_instruction), title)
        return self._with_url_tool_rule(self._tool_plain_text_prompt(title, content, cover_instruction), title)

    def _tool_rich_html_prompt(self, title: str, content: str, cover_instruction: str, rich_html: str) -> str:
        body_probe = self.body_probe(content)
        return f"""你在头条号后台操作。请按顺序发布文章：

{self._agent_recovery_rules()}

正文写入规则：
- 当前正文已由系统转换为富文本 HTML，但 HTML 内容由后端工具持有，prompt 中不再内嵌完整 HTML。
- 找到头条号正文富文本编辑器并点击聚焦后，必须调用 `paste_rich_html_body` 工具。
- 只有工具返回 ok=true 且 probe_found=true，才允许继续封面、预览或发布。
- 如果工具返回 ok=false，最多重新聚焦编辑器后再调用 1 次；仍失败则调用 done 返回 success=false，并把工具 reason 写入 failure_reason。
- 禁止在正文工具失败后使用 input/type 自行输入正文，因为完整正文只在后端工具中持有。
- 禁止使用 editor.innerHTML、DOM 注入、直接替换节点、直接设置正文 DOM 等方式伪造正文写入成功。
- 正文探针/姝ｆ枃鎺㈤拡：{body_probe}

执行步骤：
1. 打开 {self.home_url}
2. 确认已登录，并读取账号显示名记为 account_name。
3. 打开发布页：{self.publish_url}
4. 输入标题："{title}"
5. 聚焦正文编辑器，调用 `paste_rich_html_body`，确认返回 ok=true 且 probe_found=true。
6. {cover_instruction.strip()}
7. 只有页面明确显示分类等字段必填时才补齐；没有明确必填提示时不要反复滚动寻找分类。
8. 点击“预览并发布”，再点击“确认发布”。点击“确认发布”前必须先调用 prepare_publish_observer。
9. 点击“确认发布”后进入发布结果观察阶段，按下方规则处理。
"""

    def _tool_plain_text_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        body_probe = self.body_probe(content)
        return f"""你在头条号后台操作。请按顺序发布文章：

{self._agent_recovery_rules()}

正文写入规则：
- 当前正文是普通文本，正文内容由后端工具持有，优先使用确定性的工具写入。
- 找到头条号正文编辑器并点击聚焦后，必须调用 `paste_plain_text_body` 工具。
- 只有工具返回 ok=true 且 probe_found=true，才允许继续封面、预览或发布。
- 如果工具返回 ok=false，最多重新聚焦编辑器后再调用 1 次；仍失败则调用 done 返回 success=false，并把工具 reason 写入 failure_reason。
- 禁止在正文工具失败后使用 input/type 自行输入正文，因为完整正文只在后端工具中持有。
- 禁止使用 editor.innerHTML、DOM 注入、直接替换节点、直接设置正文 DOM 等方式伪造正文写入成功。
- 绂佹浣跨敤 editor.innerHTML；禁止 DOM 娉ㄥ叆。
- 正文探针/姝ｆ枃鎺㈤拡：{body_probe}

执行步骤：
1. 打开 {self.home_url}
2. 确认已登录，并读取账号显示名记为 account_name。
3. 打开发布页：{self.publish_url}
4. 输入标题："{title}"
5. 聚焦正文编辑器，调用 `paste_plain_text_body`，确认返回 ok=true 且 probe_found=true。
6. {cover_instruction.strip()}
7. 只有页面明确显示分类等字段必填时才补齐；没有明确必填提示时不要反复滚动寻找分类。
8. 点击“预览并发布”，再点击“确认发布”。点击“确认发布”前必须先调用 prepare_publish_observer。
9. 点击“确认发布”后进入发布结果观察阶段，按下方规则处理。
"""

    def _with_url_tool_rule(self, prompt: str, title: str) -> str:
        return prompt + f"""

发布结果观察阶段：
- 点击最终发布按钮前，必须先调用 prepare_publish_observer。
- 点击“确认发布”后不要立刻调用 get_published_article_url，也不要立刻 done。
- 点击“确认发布”后不要再次点击“预览并发布”或“确认发布”，除非你已经根据页面提示修复了一个可恢复问题并准备重新提交。
- 点击后调用 observe_publish_result(wait_seconds=6)，读取短暂提示缓存和当前页面证据。
- 你需要根据页面原文和上下文自行判断：成功、可恢复失败、不可恢复失败或不确定。
- 成功信号可以是页面表达已发布、提交成功、进入审核、跳转/进入作品管理页，或出现本次文章的明确成功证据。
- 如果判断已经成功但页面没有直接给出文章 URL，再调用 get_published_article_url(title="{title}")。
- 如果工具返回 found=true，必须用工具返回的 article_url 调用 done，并返回 success=true；article_url 不允许为空。
- 如果判断是可恢复失败，按页面原文修复缺失字段、分类、封面或协议勾选等问题后重新发布；同一个问题最多修复 2 次。
- 如果判断是不可恢复失败，必须调用 finish_publish_failed(reason=..., evidence=...)。
- reason 优先使用页面原文或最接近页面原文的失败原因；调用 finish_publish_failed 后不要继续操作。
- 如果达到观察上限仍无法确认发布结果，调用 finish_publish_failed，reason 写“无法确认发布结果”，evidence 写入 observe_publish_result 的关键证据。
"""

    def _agent_recovery_rules(self) -> str:
        return """Agent 执行规则：
- 你是自主 Agent，不是固定 workflow。遇到轻微、可恢复的问题时，可以自己判断并恢复，例如按钮暂时不可点、弹窗遮挡、页面加载慢、输入框未聚焦、分类未选择、预览弹窗未出现等。
- 同一个可恢复问题最多尝试 2 次。第 2 次仍然失败时，停止并调用 done 返回失败 JSON，不要继续循环。
- 遇到不可恢复问题时不要重试；如果已经进入发布结果观察阶段，必须调用 finish_publish_failed(reason=..., evidence=...) 结构化终止。不可恢复问题包括：账号被禁言、账号异常、账号无发布权限、登录失效、需要重新登录、验证码/风控验证、内容违规/审核拦截、平台明确提示禁止发布、网络长时间不可用。
- 如果页面异常回到首页、登录页、发布页初始状态，且没有明确的“发布成功/提交成功/文章已发布”证据，不要认为已经发布成功；先尝试恢复到当前步骤，最多 2 次，仍无法确认则返回失败。
- 不要因为已经点击“确认发布”就直接认为成功。只有看到明确成功提示、文章管理页出现同标题文章、或拿到有效文章 URL，才返回 success=true。
- 如果页面没有明确提示分类必填，不要为了寻找分类反复滚动；不要因为找不到分类而阻塞发布。
- 如果点击“预览并发布”后页面明确提示分类/字段必填，再按页面提示补齐；没有提示时继续确认发布。
- 处理封面时，不要点击文章正文区域的“预览”按钮；只有进入发布前最终确认时才点击“预览并发布”。
- 本地上传封面后，上传后先确认图片缩略图已出现或“确定”按钮已可点击；如果仍在上传中，等待 2 秒后重查，最多 3 次。
- 输入标题必须使用真实点击标题输入框后的 input/type/paste/send_keys 操作，不要用 evaluate 或直接设置 textarea.value 作为标题输入成功依据。
- 点击“预览并发布”前，必须确认标题计数正常且页面没有“标题不能为空”或“还需输入”提示。
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
14. 只有页面明确显示分类等字段必填时才补齐；如果没有明确必填提示，不要反复滚动寻找分类。
15. 点击“预览并发布”前调用 prepare_publish_observer，然后点击“预览并发布”，再点击“确认发布”。
16. 点击“确认发布”后进入发布结果观察阶段：调用 observe_publish_result(wait_seconds=6)，根据页面原文自行判断成功、可恢复失败、不可恢复失败或不确定。成功但没有文章 URL 时再调用 get_published_article_url 工具；不可恢复失败，必须调用 finish_publish_failed(reason=..., evidence=...)。

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
7. 只有页面明确显示分类等字段必填时才补齐；如果没有明确必填提示，不要反复滚动寻找分类。
8. 点击“预览并发布”前调用 prepare_publish_observer，然后点击“预览并发布”，再点击“确认发布”。
9. 点击“确认发布”后进入发布结果观察阶段：调用 observe_publish_result(wait_seconds=6)，根据页面原文自行判断成功、可恢复失败、不可恢复失败或不确定。成功但没有文章 URL 时再调用 get_published_article_url 工具；不可恢复失败，必须调用 finish_publish_failed(reason=..., evidence=...)。

正文长度：{content_length} 字符
正文预览：{content_preview}
"""

    def _plain_text_prompt(self, title: str, content: str, cover_instruction: str) -> str:
        body_probe = self.body_probe(content)
        return f"""你在头条号后台操作。请按顺序发布文章：

{self._agent_recovery_rules()}

1. 打开 {self.home_url}
2. 确认已登录，并读取账号显示名记为 account_name。
3. 打开发布页：{self.publish_url}
4. 输入标题："{title}"
5. 正文写入优先使用剪贴板粘贴，严格按顺序执行：
   - 点击正文编辑器，使其获得焦点。
   - 使用 evaluate 执行下面的 JavaScript，把 `<content>` 中的纯文本写入浏览器剪贴板：
```javascript
async (bodyText) => {{
  try {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      await navigator.clipboard.writeText(bodyText);
      return {{ ok: true, method: "navigator.clipboard.writeText" }};
    }}
  }} catch (error) {{}}
  const textarea = document.createElement("textarea");
  textarea.value = bodyText;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  return {{ ok: !!ok, method: "execCommand" }};
}}
```
   - 保持正文编辑器焦点，执行 `send_keys("Control+v")` 粘贴正文。
   - 粘贴后必须读取正文编辑器自身的可见文本，确认正文探针 "{body_probe}" 已出现。
   - 如果粘贴失败，或者正文探针未命中，再使用 input/type 方式写入正文。
   - 使用 input/type 回退后仍必须确认正文探针 "{body_probe}" 已出现。
   - 禁止使用 editor.innerHTML、DOM 注入、直接替换节点、直接设置正文 DOM 等方式伪造正文写入成功。
   - 正文探针未命中时禁止继续点击封面、预览或发布。
正文内容：
<content>
{content}
</content>
6. {cover_instruction.strip()}
7. 只有页面明确显示分类等字段必填时才补齐；如果没有明确必填提示，不要反复滚动寻找分类。
8. 点击“预览并发布”前调用 prepare_publish_observer，然后点击“预览并发布”，再点击“确认发布”。
9. 点击“确认发布”后进入发布结果观察阶段：调用 observe_publish_result(wait_seconds=6)，根据页面原文自行判断成功、可恢复失败、不可恢复失败或不确定。成功但没有文章 URL 时再调用 get_published_article_url 工具；不可恢复失败，必须调用 finish_publish_failed(reason=..., evidence=...)。
"""
