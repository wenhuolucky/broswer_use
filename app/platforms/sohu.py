"""Sohu platform configuration."""

from __future__ import annotations

import os
import re
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
        body = self._plain_text_body(content)
        body_probe = self._body_probe(body)
        if is_markdown and rich_html:
            return self._rich_html_ready_prompt(
                title=title,
                body_probe=body_probe,
                cover_instruction=cover_instruction,
                rich_html=rich_html,
            )
        return self._plain_text_prompt(
            title=title,
            body=body,
            content=content,
            body_probe=body_probe,
            cover_instruction=cover_instruction,
        )

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
  2. https://m.sohu.com/a/...
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
1. 打开搜狐号后台：{self.home_url}
2. 确认当前账号已登录；如果未登录，停止并返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 进入文章发布入口。
5. 填写标题：{title}
6. 找到搜狐号正文富文本编辑器，点击使其获得焦点。
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
9. 等待 1-2 秒让富文本渲染完成。
10. 发布前正文校验（强制）：
    - 必须读取正文编辑器的可见文本（document.body.innerText / 编辑器 innerText）。
    - 正文编辑器文本必须非空，并且包含这个正文关键片段："{body_probe}"。
    - 如果未检测到正文，重新点击编辑器并再次执行 7、8 步写入正文，最多重试 2 次。
    - 2 次后仍未检测到正文，立即调用 done 返回 success=false，failure_reason="搜狐号正文写入失败，发布前未检测到正文内容"。
    - 未通过正文校验时，禁止继续点击"下一步"、封面设置或发布。
11. 如果页面需要点击"下一步"，点击进入发布设置页。
12. {cover_instruction.strip()}
13. 完成分类等必填项。摘要可以从正文前 80 到 120 字生成。
14. 点击"发布"。
15. 发布后确认出现提交审核、审核中或发布成功信号。
16. 获取文章预览链接或移动端文章链接。
17. 拿到 URL 后，调用 done 返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
18. 如果没有拿到 URL，调用 done 返回：
    {{"success": false, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": "搜狐号提交审核成功，但未获取到文章 URL", "publish_signal": ""}}

封面要求：
{cover_instruction.strip()}
{self._cover_fallback_rule()}

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
  2. https://m.sohu.com/a/...
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
1. 打开搜狐号后台：{self.home_url}
2. 确认当前账号已登录；如果未登录，停止并返回失败。
3. 读取当前账号显示名，记为 account_name。
4. 进入文章发布入口。
5. 填写标题：{title}
6. 搜狐号正文写入策略（纯文本路径）：
   - 先点击搜狐号正文编辑器，让编辑器获得焦点。
   - 将下面 `<content>` 中的纯文本写入剪贴板（navigator.clipboard.writeText 或 execCommand），再使用 Ctrl+V 粘贴。
   - 如果剪贴板不可用，可以使用键盘输入或页面支持的普通文本粘贴方式。
7. 发布前正文校验（强制）：
   - 必须读取正文编辑器的可见文本。
   - 正文编辑器文本必须非空，并且包含这个正文关键片段："{body_probe}"。
   - 如果未检测到正文，重新点击编辑器并再次写入正文，最多重试 2 次。
   - 2 次后仍未检测到正文，立即调用 done 返回 success=false，failure_reason="搜狐号正文写入失败，发布前未检测到正文内容"。
   - 未通过正文校验时，禁止继续点击"下一步"、封面设置或发布。
8. 如果页面需要点击"下一步"，点击进入发布设置页。
9. {cover_instruction.strip()}
10. 完成分类等必填项。摘要可以从正文前 80 到 120 字生成。
11. 点击"发布"。
12. 发布后确认出现提交审核、审核中或发布成功信号。
13. 获取文章预览链接或移动端文章链接。
14. 拿到 URL 后，调用 done 返回合法 JSON 字符串：
    {{"success": true, "account_name": "步骤3读到的账号名", "article_url": "拿到的搜狐文章URL", "failure_reason": "", "publish_signal": "submitted_for_review"}}
15. 如果没有拿到 URL，调用 done 返回：
    {{"success": false, "account_name": "步骤3读到的账号名", "article_url": "", "failure_reason": "搜狐号提交审核成功，但未获取到文章 URL", "publish_signal": ""}}

封面要求：
{cover_instruction.strip()}
{self._cover_fallback_rule()}

正文内容：
<content>
{body}
</content>
"""

    @staticmethod
    def _cover_fallback_rule() -> str:
        return """

搜狐号封面特殊要求（覆盖上述封面流程中"本地上传"那一步的判定）：
- 触发本地上传文件后，如果观察到"上传中"状态持续不消失（页面出现"上传中"文字、loading 旋转图标、spinner 动画、进度条一直动但没有完成提示等），不要再继续等待。
- 此时实际图片已经上传完成，并已经同步到"素材库"。
- 关闭本地上传对话框或进度弹窗，切换到"素材库"标签，浏览素材库列表，**选择列表第一张图片**作为封面。
- 选完后确认页面上显示的是单封面预览，再继续后续步骤。
- 如果素材库列表为空或加载失败，再尝试其他兜底；否则不要再循环封面上传。
- 本地路径失败不一定是"上传中"卡死；如果 file input 报错或文件类型不被接受，按页面提示处理；只有"上传中"持续不消失才走素材库 fallback。
"""

    @staticmethod
    def _plain_text_body(content: str) -> str:
        text = str(content or "")
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"[*_`>#-]+", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _body_probe(body: str) -> str:
        compact = "".join(str(body or "").split())
        return compact[:30]
