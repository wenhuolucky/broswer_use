"""Sohu platform configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.platforms.base import PlatformConfig


@dataclass
class SohuPlatform(PlatformConfig):
    name: str = "sohu"
    home_url: str = "https://mp.sohu.com"
    publish_url: str = "https://mp.sohu.com/mpfe/v4/contentManagement/news/addarticle?contentStatus=1"
    auth_domains: list = field(default_factory=lambda: [".sohu.com", "mp.sohu.com", "www.sohu.com"])
    account_cookies: list = field(default_factory=lambda: ["SUV", "ppinf", "ppmdig"])
    # 搜狐登录态的标志性 cookie：passport 登录成功后才下发 ppinf/ppmdig。
    # 不能留空——留空会让 has_login_cookie 把"任意 .sohu.com cookie"都当成已登录，
    # 而搜狐在登录页加载时就会种下匿名 cookie（如 SUV），导致刚打开登录页、还没登录
    # 就被自动绑定。SUV 是匿名访客标识，只用于 extract_native_key 兜底，不作登录依据。
    login_cookie_names: list = field(default_factory=lambda: ["ppinf", "ppmdig"])

    def extract_native_key(self, cookies: list) -> str:
        # 搜狐用 passport 标识 ppinf 作为账号去重键，回退到 ppmdig / SUV。
        by_name = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
        for name in ("ppinf", "ppmdig", "SUV"):
            if by_name.get(name):
                return str(by_name[name])
        return ""

    def article_url_account_id(self, metadata: dict) -> str:
        # 搜狐号数字 id 取自该渠道 metadata 里登录期抓到的 account_number。
        return str((metadata or {}).get("account_number", "") or "").strip()

    def get_agent_prompt(
        self,
        title: str,
        content: str,
        cover_instruction: str,
        body_image_instruction: str,
        is_markdown: bool,
        rich_html: str = None,
    ) -> str:
        body = self.strip_markdown(content)
        body_probe = self.body_probe(content)
        # 搜狐号只有单封面：用搜狐号专属指令替换 kernel 的通用指令（去掉三封面/五封面等头条概念）
        if cover_instruction and "使用 file input 上传下面这个本地文件" in cover_instruction:
            cover_instruction = (
                '\n7. 设置封面图片，严格按顺序执行：\n'
                '   - 在封面设置区域找到并点击封面区域。\n'
                '   - 弹出的对话框默认是正文图片，点击对话框上边切换为本地上传。\n'
                '   - 使用 file input 上传下面这个本地文件：\n'
                f'     {self._extract_cover_path(cover_instruction)}\n'
                '   - 上传后先确认图片缩略图已出现或"确定"按钮已可点击。\n'
                '   - 如果按钮暂不可点，等待 2 秒后重新检查；最多检查 3 次，不要无限等待。\n'
                '   - 确认页面上显示封面预览后，再点击"确定"。\n'
            )
        if is_markdown and rich_html:
            return self._tool_rich_html_prompt(
                title=title,
                body=body,
                body_probe=body_probe,
                cover_instruction=cover_instruction,
                rich_html=rich_html,
            )
        return self._tool_plain_text_prompt(
            title=title,
            body=body,
            content=content,
            body_probe=body_probe,
            cover_instruction=cover_instruction,
        )

    @staticmethod
    def _extract_cover_path(cover_instruction: str) -> str:
        """从通用 cover_instruction 中提取封面文件路径。"""
        for line in cover_instruction.splitlines():
            stripped = line.strip()
            if stripped.startswith(("/", "C:/", "D:/", "E:/", "/tmp/", "/root/")):
                return stripped
        return ""

    def _tool_rich_html_prompt(
        self,
        title: str,
        body: str,
        body_probe: str,
        cover_instruction: str,
        rich_html: str,
    ) -> str:
        return f"""请在搜狐号后台发布一篇文章。
关键规则：
- 平台是搜狐号，不是今日头条。
- 外部接口要求成功结果必须包含 article_url。
- 必须获取真实 URL，不要编造 URL。
- 如果发布成功页面没有直接显示 URL，必须调用工具 `get_published_article_url(title="{title}")` 回查作品列表。
- 当前正文已由系统转换为富文本 HTML，但 HTML 内容由后端工具持有，prompt 中不再内嵌完整 HTML。
- 找到搜狐号正文富文本编辑器并点击聚焦后，必须调用 `paste_rich_html_body` 工具。
- 只有工具返回 ok=true 且 probe_found=true，才允许继续“下一步”、封面设置或发布。
- 如果工具返回 ok=false，最多重新聚焦编辑器后再调用 1 次；仍失败则调用 done 返回 success=false，并把工具 reason 写入 failure_reason。
- 禁止在正文工具失败后使用 input/type 自行输入正文，因为完整正文只在后端工具中持有。
- 禁止使用 editor.innerHTML、DOM 注入、insertHTML、直接替换节点、直接设置正文 DOM 等方式写入正文。
- 正文探针：{body_probe}

Agent 执行规则：
- 同一个可恢复问题最多尝试 2 次；第 2 次仍失败时，停止并调用 done 返回失败 JSON。
- 不要因为已经点击"发布"就直接认为成功。只有看到明确成功信号或拿到有效文章 URL，才返回 success=true。
- done 返回必须是合法 JSON 字符串。

执行步骤：
1. 打开搜狐号发布页：{self.publish_url}
2. 确认当前账号已登录（页面显示账号名或编辑区可用）；如果未登录或页面重定向到登录页，停止并返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 在标题输入框输入标题："{title}"。使用 input/type 操作，不要调用工具，不要 evaluate 直接设置 value。
5. 聚焦搜狐号正文编辑器，调用 `paste_rich_html_body`，确认返回 ok=true 且 probe_found=true。
6. {cover_instruction.strip()}
{self._sohu_cover_rules(cover_instruction)}
7. 下滑到页面底部，点击"发布"按钮完成发布。
8. 点击"发布"后不要等待页面成功提示，也不要重复点击发布按钮；直接调用 `get_published_article_url(title="{title}")` 工具，并用工具返回的 article_url 调用 done。
"""

    def _tool_plain_text_prompt(
        self,
        title: str,
        body: str,
        content: str,
        body_probe: str,
        cover_instruction: str,
    ) -> str:
        return f"""请在搜狐号后台发布一篇文章。
关键规则：
- 平台是搜狐号，不是今日头条。
- 外部接口要求成功结果必须包含 article_url。
- 必须获取真实 URL，不要编造 URL。
- 如果发布成功页面没有直接显示 URL，必须调用工具 `get_published_article_url(title="{title}")` 回查作品列表。
- 当前正文是普通文本，正文内容由后端工具持有，优先使用确定性的工具写入。
- 找到搜狐号正文编辑器并点击聚焦后，必须调用 `paste_plain_text_body` 工具。
- 只有工具返回 ok=true 且 probe_found=true，才允许继续“下一步”、封面设置或发布。
- 如果工具返回 ok=false，最多重新聚焦编辑器后再调用 1 次；仍失败则调用 done 返回 success=false，并把工具 reason 写入 failure_reason。
- 禁止在正文工具失败后使用 input/type 自行输入正文，因为完整正文只在后端工具中持有。
- 禁止使用 editor.innerHTML、DOM 注入、insertText、直接替换节点、直接设置正文 DOM 等方式写入正文。
- 正文探针：{body_probe}

Agent 执行规则：
- 同一个可恢复问题最多尝试 2 次；第 2 次仍失败时，停止并调用 done 返回失败 JSON。
- 不要因为已经点击"发布"就直接认为成功。只有看到明确成功信号或拿到有效文章 URL，才返回 success=true。
- done 返回必须是合法 JSON 字符串。

执行步骤：
1. 打开搜狐号发布页：{self.publish_url}
2. 确认当前账号已登录（页面显示账号名或编辑区可用）；如果未登录或页面重定向到登录页，停止并返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 在标题输入框输入标题："{title}"。使用 input/type 操作，不要调用工具，不要 evaluate 直接设置 value。
5. 聚焦搜狐号正文编辑器，调用 `paste_plain_text_body`，确认返回 ok=true 且 probe_found=true。
6. {cover_instruction.strip()}
{self._sohu_cover_rules(cover_instruction)}
7. 下滑到页面底部，点击"发布"按钮完成发布。
8. 点击"发布"后不要等待页面成功提示，也不要重复点击发布按钮；直接调用 `get_published_article_url(title="{title}")` 工具，并用工具返回的 article_url 调用 done。
"""

    def _rich_html_ready_prompt(
        self,
        title: str,
        body_probe: str,
        cover_instruction: str,
        rich_html: str,
    ) -> str:
        html_length = len(rich_html)
        return f"""请在搜狐号后台发布一篇文章。

关键规则：
- 平台是搜狐号，不是今日头条。
- 外部接口要求成功结果必须包含 article_url。
- 搜狐号发布后如果页面显示提交审核、审核中或发布成功，并且能拿到文章 URL，也算发布成功。
- 必须获取真实 URL，不要编造 URL。
- 可接受的 URL 包括：
  1. https://mp.sohu.com/h5/v2/newsPreview?id=...&type=article
  2. https://mp.sohu.com/mpfe/v4/contentManagement/.../news/articlepreview?id=...&accountId=...
  3. https://m.sohu.com/a/...
  4. https://www.sohu.com/a/...
- 如果发布成功页没有直接显示 URL，必须调用工具 `get_published_article_url(title="{title}")` 回查作品列表；found=true 时使用工具返回的 article_url。
- 如果只看到提交审核成功但没有拿到 URL，必须返回 success=false。
- 搜狐号编辑器对 Ctrl+V 粘贴富文本友好，标题、加粗、列表、链接、图片都会自动渲染。
- 本次正文导入只允许一条路径：先写入浏览器剪贴板，再执行 Ctrl+V 粘贴。
- 禁止通过 `editor.innerHTML`、DOM 注入、直接替换节点、直接拼接 HTML 片段等方式写入正文。
- 如果剪贴板写入失败，或者 Ctrl+V 后正文完全没有粘贴进去，2 次重试后返回失败。
- 如果富文本结构不完全理想，例如段落、列表、加粗、换行没有完全按预期渲染，不要长时间反复检查；记录现象后仍然继续执行发布。
- 重点确认正文已经进入编辑器，标题正确，图片尽可能展示，然后继续发布。

Agent 执行规则：
- 你是自主 Agent，不是固定 workflow。遇到轻微、可恢复的问题时，可以自己判断并恢复，例如按钮暂时不可点、弹窗遮挡、页面加载慢、输入框未聚焦、分类未选择、预览弹窗未出现等。
- 同一个可恢复问题最多尝试 2 次。第 2 次仍然失败时，停止并调用 done 返回失败 JSON，不要继续循环。
- 遇到不可恢复问题时不要重试，立即调用 done 返回失败 JSON。不可恢复问题包括：账号被禁言、账号异常、账号无发布权限、登录失效、需要重新登录、验证码/风控验证、内容违规/审核拦截、平台明确提示禁止发布、网络长时间不可用。
- 不要因为已经点击"发布"就直接认为成功。只有看到明确成功提示或拿到有效文章 URL，才返回 success=true。
- 如果看到明确失败提示，failure_reason 必须尽量使用页面原文；如果没有页面原文，再用简短中文总结原因。
- done 返回必须是合法 JSON 字符串，格式为：
  {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
  或
  {{"success": false, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": "失败原因", "publish_signal": ""}}

执行步骤：
1. 打开搜狐号发布页：{self.publish_url}
2. 确认当前账号已登录（页面显示账号名或编辑区可用）；如果未登录或页面重定向到登录页，停止并返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 在标题输入框输入标题："{title}"。使用 input/type 操作，输入后读取实际值校验与预期一致；不一致最多重试 2 次，仍失败则 done 返回 success=false。
5. 找到搜狐号正文富文本编辑器，点击使其获得焦点。
6. 使用 evaluate 执行下面的 JavaScript，把完整 HTML 写入浏览器剪贴板：
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
6. 保持编辑器焦点，使用 `send_keys("Control+v")` 进行粘贴，也就是执行 Ctrl+V。
7. 等待 1-2 秒让富文本渲染完成。
8. 发布前正文校验（强制）：
    - 必须读取**正文编辑器自身的**可见文本（找 `[contenteditable="true"]` 元素，挑可见文本最长的那个，不要读 document.body）。
    - 正文编辑器文本必须非空，并且包含这个正文关键片段："{body_probe}"。
    - 如果未检测到正文，**先按以下顺序重试 3 次**，再考虑返回失败：
      - 重试 A：重新点击编辑器 + 重新执行步骤 6、7（JS 剪贴板 + Ctrl+V）。
      - 重试 B：在编辑器仍 focus 状态下，使用 evaluate 执行 `document.execCommand('insertHTML', false, htmlContent)`，其中 `htmlContent` 是下面 `========== HTML BEGIN ==========` 和 `========== HTML END ==========` 之间的完整 HTML 字符串。**这是绕过系统剪贴板的 DOM 注入方式，不依赖 Ctrl+V 事件能否被编辑器接收**。
      - 重试 C：如果步骤 B 的 evaluate 也没有让编辑器内容出现，再清空 + 重新执行步骤 6、7。
    - 3 次重试后仍未检测到正文，立即调用 done 返回 success=false，failure_reason="搜狐号正文写入失败，发布前未检测到正文内容"。
    - 未通过正文校验时，禁止继续封面设置或发布。
9. {cover_instruction.strip()}
10. {self._sohu_cover_rules(cover_instruction)}
11. 下滑到页面底部，点击"发布"按钮完成发布。
12. 点击"发布"后不要等待页面成功提示，也不要重复点击发布按钮；直接调用 `get_published_article_url(title="{title}")` 工具，并用工具返回的 article_url 调用 done，返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
13. 如果工具返回 found=false，调用 done 返回：
    {{"success": false, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": "搜狐号发布成功，但未获取到文章 URL", "publish_signal": ""}}

完整 HTML 长度：{html_length} 字符

========== HTML BEGIN ==========
{rich_html}
========== HTML END ==========
"""

    def _plain_text_prompt(
        self,
        title: str,
        body: str,
        content: str,
        body_probe: str,
        cover_instruction: str,
    ) -> str:
        return f"""请在搜狐号后台发布一篇文章。

关键规则：
- 平台是搜狐号，不是今日头条。
- 外部接口要求成功结果必须包含 article_url。
- 搜狐号发布后如果页面显示提交审核、审核中或发布成功，并且能拿到文章 URL，也算发布成功。
- 必须获取真实 URL，不要编造 URL。
- 可接受的 URL 包括：
  1. https://mp.sohu.com/h5/v2/newsPreview?id=...&type=article
  2. https://mp.sohu.com/mpfe/v4/contentManagement/.../news/articlepreview?id=...&accountId=...
  3. https://m.sohu.com/a/...
  4. https://www.sohu.com/a/...
- 如果发布成功页没有直接显示 URL，必须调用工具 `get_published_article_url(title="{title}")` 回查作品列表；found=true 时使用工具返回的 article_url。
- 如果只看到提交审核成功但没有拿到 URL，必须返回 success=false。
- 本次系统没有提供富文本 HTML。只能使用纯文本写入正文。
- 不要把 HTML 源码写入搜狐号编辑器。
- 优先将下面 `<content>` 中的纯文本写入剪贴板（navigator.clipboard.writeText 或 execCommand），再使用 Ctrl+V 粘贴。
- 如果剪贴板不可用，可以使用键盘输入或页面支持的普通文本粘贴方式。

Agent 执行规则：
- 你是自主 Agent，不是固定 workflow。遇到轻微、可恢复的问题时，可以自己判断并恢复，例如按钮暂时不可点、弹窗遮挡、页面加载慢、输入框未聚焦、分类未选择、预览弹窗未出现等。
- 同一个可恢复问题最多尝试 2 次。第 2 次仍然失败时，停止并调用 done 返回失败 JSON，不要继续循环。
- 遇到不可恢复问题时不要重试，立即调用 done 返回失败 JSON。不可恢复问题包括：账号被禁言、账号异常、账号无发布权限、登录失效、需要重新登录、验证码/风控验证、内容违规/审核拦截、平台明确提示禁止发布、网络长时间不可用。
- 不要因为已经点击"发布"就直接认为成功。只有看到明确成功提示或拿到有效文章 URL，才返回 success=true。
- 如果看到明确失败提示，failure_reason 必须尽量使用页面原文；如果没有页面原文，再用简短中文总结原因。
- done 返回必须是合法 JSON 字符串，格式为：
  {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
  或
  {{"success": false, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": "失败原因", "publish_signal": ""}}

执行步骤：
1. 打开搜狐号发布页：{self.publish_url}
2. 确认当前账号已登录（页面显示账号名或编辑区可用）；如果未登录或页面重定向到登录页，停止并返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 在标题输入框输入标题："{title}"。使用 input/type 操作，输入后读取实际值校验与预期一致；不一致最多重试 2 次，仍失败则 done 返回 success=false。
5. 搜狐号正文写入策略（纯文本路径）：
   - 先点击搜狐号正文编辑器，让编辑器获得焦点。
   - 将下面 `<content>` 中的纯文本写入剪贴板（navigator.clipboard.writeText 或 execCommand），再使用 Ctrl+V 粘贴。
   - 如果剪贴板不可用，可以使用键盘输入或页面支持的普通文本粘贴方式。
6. 发布前正文校验（强制）：
   - 必须读取**正文编辑器自身的**可见文本（找 `[contenteditable="true"]` 元素，挑可见文本最长的那个，不要读 document.body）。
   - 正文编辑器文本必须非空，并且包含这个正文关键片段："{body_probe}"。
   - 如果未检测到正文，**先按以下顺序重试 3 次**，再考虑返回失败：
     - 重试 A：重新点击编辑器 + 重新执行步骤 5（剪贴板 + Ctrl+V）。
     - 重试 B：在编辑器仍 focus 状态下，使用 evaluate 执行 `document.execCommand('insertText', false, bodyText)`，其中 `bodyText` 是下面 `<content>` 标签内的纯文本字符串。**这是绕过系统剪贴板的 DOM 注入方式**。
     - 重试 C：清空 + 重新执行步骤 5。
   - 3 次重试后仍未检测到正文，立即调用 done 返回 success=false，failure_reason="搜狐号正文写入失败，发布前未检测到正文内容"。
   - 未通过正文校验时，禁止继续封面设置或发布。
7. {cover_instruction.strip()}
8. {self._sohu_cover_rules(cover_instruction)}
9. 下滑到页面底部，点击"发布"按钮完成发布。
10. 点击"发布"后不要等待页面成功提示，也不要重复点击发布按钮；直接调用 `get_published_article_url(title="{title}")` 工具，并用工具返回的 article_url 调用 done，返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
11. 如果工具返回 found=false，调用 done 返回：
    {{"success": false, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": "搜狐号发布成功，但未获取到文章 URL", "publish_signal": ""}}

正文内容：
<content>
{body}
</content>
"""

    @staticmethod
    def _sohu_cover_rules(cover_instruction: str) -> str:
        """搜狐号封面指导：Agent 自主操作 file input 完成封面上传（不依赖专用工具）。"""
        return """
搜狐号封面设置补充说明：
- 上面 cover_instruction 的封面步骤必须严格按顺序执行，不要跳步。
- 正文写入工具返回 ok=true 且 probe_found=true 后，先在编辑页找到封面设置区域并点击。
- 封面设置失败时最多重试 1 次，仍失败则跳过封面，继续分类、摘要和发布。
"""
