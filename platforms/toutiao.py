"""
头条号平台配置
"""

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

    def get_agent_prompt(self, title: str, content: str,
                         cover_instruction: str,
                         body_image_instruction: str,
                         is_markdown: bool) -> str:
        if is_markdown:
            return self._markdown_prompt(title, content, cover_instruction, body_image_instruction)
        else:
            return self._plain_text_prompt(title, content, cover_instruction, body_image_instruction)

    def _markdown_prompt(self, title, content, cover_instruction, body_image_instruction):
        return f"""请按顺序完成以下步骤来发布一篇文章：

【第一阶段：Markdown 转富文本】
1. 首先导航到: https://markdowntorichtext.com/zh/
2. 等待页面加载完成。
3. 找到页面的 Markdown 输入区域（通常是一个大的 textarea 或 contenteditable 区域）。
4. 使用以下方法将 Markdown 内容粘贴到输入区域：
   - 使用 evaluate 执行 JavaScript：
     const textarea = document.querySelector('textarea') || document.querySelector('[contenteditable]');
     if (textarea) {{ textarea.value = `{content}`.slice(0, 50000); textarea.dispatchEvent(new Event('input', {{ bubbles: true }})); }}
   - 如果上述方法不行，尝试用 evaluate 直接设置输入区域的 value
5. 等待页面渲染预览（右侧或下方应显示格式化后的预览）。
6. 找到页面上的"复制"按钮（Copy / Copy rich text / 复制按钮），点击它。
   - 如果页面上有多个复制按钮，尝试点击那个复制富文本的按钮。
   - 复制成功后，页面上通常会有"已复制"或"Rich text copied to clipboard"的提示。
7. 确认剪贴板中已有富文本内容。

【第二阶段：发布到头条号】
8. 导航到头条号后台: {self.home_url}
9. 确认已登录后台。如果看到登录页面，停止并提示用户重新 --setup 登录。
   - 在首页/后台读取当前登录账号的显示名（通常在页面右上角头像旁边，或个人中心，例如"头条号名称"/昵称），记录为 account_name。
10. 导航到发布文章页面: {self.publish_url}
11. 等待页面加载完成。
12. 找到文章标题输入框，输入标题: "{title}"
13. 找到文章正文编辑区域（通常是富文本编辑器 body.contenteditable）。
14. 使用以下方法粘贴富文本内容：
    - 点击编辑区域获取焦点
    - 使用 evaluate 执行 JavaScript 粘贴富文本（模拟粘贴 HTML）：
      const editor = document.querySelector('[contenteditable=true]');
      if (editor) {{
        editor.focus();
        // 使用 execCommand 插入 HTML（模拟 Ctrl+V 粘贴富文本）
        document.execCommand('insertHTML', false, `从剪贴板获取的富文本HTML`);
      }}
    - 如果上述方法不行，尝试先导航回 markdowntorichtext.com 页面，手动 Ctrl+A 全选富文本预览区域，Ctrl+C 复制，然后回到头条号编辑器 Ctrl+V 粘贴{body_image_instruction}
15. 等待编辑器渲染，确认内容已正确显示（标题、段落、加粗、图片等）。
16. {cover_instruction.strip()}
17. 检查分类等必填项是否缺失。
18. 找到页面底部的"预览并发布"按钮（它是浮动固定在页面底部的）。
    - 如果看不到该按钮，尝试缩小页面或等待页面加载完成。
19. 点击"预览并发布"按钮。等待预览对话框弹出，然后点击对话框中的"确认发布"按钮。
    - **重要**：点击"确认发布"后，无论页面显示什么（包括"草稿保存中"、无明显变化），
      都**立即调用 done 并标记 success=true**，不要再回到编辑页或重新输入内容。
    - 最多尝试 2 次"预览并发布"→"确认发布"。2 次后无论结果如何都调用 done。
    - 只有出现明确的错误提示（红色Toast、弹窗报错、登录页）时，才标记 success=false。
20. 调用 done 动作时，请把最终结果作为 JSON 字符串返回，格式为:
    {{"success": true/false, "account_name": "步骤9读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}

重要提示：
- 头条号编辑器是 contenteditable 的富文本区域，可能需要先点击编辑区域获取焦点再粘贴。
- 遇到弹窗/引导/广告先关闭再继续。
- 每个步骤完成后确认页面响应再继续下一步。
- 发布按钮是浮动固定在页面底部的，如果看不到，尝试等待或缩小页面。
- 设置封面时：先点封面区域的"+"加号 → 弹出对话框后点"本地上传" → 使用 file input 上传
- 预览对话框出现后，寻找文字为"确认发布"的按钮（不是"草稿已保存"），仔细找到正确的按钮并点击。
- 预览对话框中，"确认发布"按钮通常在对话框底部，请耐心滚动或查找。
- **点击"确认发布"后立即停止，不要反复搜索内容管理页或重新发布。**
- Markdown 转换网站：https://markdowntorichtext.com/zh/
- 如果复制按钮不起作用，尝试点击页面上的 Copy 或 "Copy rich text" 按钮。
"""

    def _plain_text_prompt(self, title, content, cover_instruction, body_image_instruction):
        return f"""你在头条号后台(mp.toutiao.com)操作。请按顺序完成以下步骤来发布一篇文章：

1. 首先导航到: {self.home_url}
2. 确认已登录后台。如果看到登录页面，停止并提示用户重新 --setup 登录。
   - 在首页/后台读取当前登录账号的显示名（通常在页面右上角头像旁边，或个人中心，例如"头条号名称"/昵称），记录为 account_name。
3. 导航到发布文章页面: {self.publish_url}
4. 等待页面加载完成。
5. 找到文章标题输入框，输入标题: "{title}"
6. 找到文章正文编辑区域（通常是富文本编辑器 body.contenteditable），输入以下内容:{body_image_instruction}

<content>
{content}
</content>{cover_instruction}
8. 检查分类等必填项是否缺失。
9. 找到页面底部的"预览并发布"按钮（它是浮动固定在页面底部的）。
   - 如果看不到该按钮，尝试缩小页面或等待页面加载完成。
10. 点击"预览并发布"按钮。等待预览对话框弹出，然后点击对话框中的"确认发布"按钮。
    - **重要**：点击"确认发布"后，无论页面显示什么（包括"草稿保存中"、无明显变化），
      都**立即调用 done 并标记 success=true**，不要再回到编辑页或重新输入内容。
    - 最多尝试 2 次"预览并发布"→"确认发布"。2 次后无论结果如何都调用 done。
    - 只有出现明确的错误提示（红色Toast、弹窗报错、登录页）时，才标记 success=false。
11. 调用 done 动作时，请把最终结果作为 JSON 字符串返回，格式为:
    {{"success": true/false, "account_name": "步骤2读取到的账号名", "article_url": "", "failure_reason": "失败原因（若失败）"}}

重要提示：
- 头条号编辑器是 contenteditable 的富文本区域，可能需要先点击编辑区域获取焦点再输入。
- 遇到弹窗/引导/广告先关闭再继续。
- 每个步骤完成后确认页面响应再继续下一步。
- 发布按钮是浮动固定在页面底部的，如果看不到，尝试等待或缩小页面。
- 设置封面时：先点封面区域的"+"加号 → 弹出对话框后点"本地上传" → 使用 file input 上传
- 预览对话框出现后，寻找文字为"确认发布"的按钮（不是"草稿已保存"），仔细找到正确的按钮并点击。
- 预览对话框中，"确认发布"按钮通常在对话框底部，请耐心滚动或查找。
- **点击"确认发布"后立即停止，不要反复搜索内容管理页或重新发布。**
"""
