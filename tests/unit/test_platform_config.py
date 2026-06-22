"""平台配置测试（app/platforms/base.py + sohu/config.py + toutiao/config.py）。

聚焦不触发浏览器的纯配置逻辑：account id 解析、正文探针、cookie 校验、账号名提取。
get_agent_prompt 属于 prompt 工程，文本易变，这里不做断言。
"""

from __future__ import annotations

from app.platforms.base import PlatformConfig
from app.platforms.sohu.config import SohuPlatform
from app.platforms.toutiao.config import ToutiaoPlatform


class TestSohuArticleUrlAccountId:
    def test_reads_account_number_from_metadata(self) -> None:
        assert SohuPlatform().article_url_account_id({"account_number": "122702850"}) == "122702850"

    def test_strips_whitespace(self) -> None:
        assert SohuPlatform().article_url_account_id({"account_number": " 111 "}) == "111"

    def test_missing_metadata_returns_empty(self) -> None:
        assert SohuPlatform().article_url_account_id({}) == ""
        assert SohuPlatform().article_url_account_id(None) == ""


class TestToutiaoArticleUrlAccountId:
    def test_uses_base_default_empty(self) -> None:
        # 头条不需要 account id，沿用 base 默认空串
        assert ToutiaoPlatform().article_url_account_id({"account_number": "x"}) == ""


class TestToutiaoPrompt:
    def test_plain_text_prompt_avoids_category_scroll_loop(self) -> None:
        prompt = ToutiaoPlatform().get_agent_prompt(
            title="测试标题",
            content="测试正文",
            cover_instruction="封面设置：不需要手动设置封面。",
            body_image_instruction="",
            is_markdown=False,
        )

        assert "如果页面没有明确提示分类必填，不要为了寻找分类反复滚动" in prompt
        assert "不要因为找不到分类而阻塞发布" in prompt

    def test_plain_text_prompt_warns_not_to_click_article_preview_for_cover(self) -> None:
        prompt = ToutiaoPlatform().get_agent_prompt(
            title="测试标题",
            content="测试正文",
            cover_instruction="设置封面图片，严格按顺序执行：\n- 在封面设置区域找到并点击加号图标。",
            body_image_instruction="",
            is_markdown=False,
        )

        assert "不要点击文章正文区域的“预览”按钮" in prompt
        assert "上传后先确认图片缩略图已出现或“确定”按钮已可点击" in prompt

    def test_plain_text_prompt_requires_real_title_input_and_post_confirm_lookup(self) -> None:
        prompt = ToutiaoPlatform().get_agent_prompt(
            title="测试标题",
            content="测试正文",
            cover_instruction="封面设置：不需要手动设置封面。",
            body_image_instruction="",
            is_markdown=False,
        )

        assert "不要用 evaluate 或直接设置 textarea.value 作为标题输入成功依据" in prompt
        assert "标题计数正常且页面没有“标题不能为空”" in prompt
        assert "确认发布”后不要再次点击“预览并发布”或“确认发布”" in prompt
        assert "直接调用 get_published_article_url" in prompt

    def test_plain_text_prompt_prefers_clipboard_paste_with_input_fallback(self) -> None:
        prompt = ToutiaoPlatform().get_agent_prompt(
            title="测试标题",
            content="这是用于正文探针的测试正文",
            cover_instruction="封面设置：不需要手动设置封面。",
            body_image_instruction="",
            is_markdown=False,
        )

        assert "paste_plain_text_body" in prompt
        assert "navigator.clipboard.writeText" not in prompt
        assert 'send_keys("Control+v")' not in prompt
        assert "正文探针" in prompt
        assert "ok=false" in prompt
        assert "工具返回 ok=false，最多重新聚焦编辑器后再调用 1 次；仍失败时再使用 input/type 方式写入正文" not in prompt
        assert "禁止使用 editor.innerHTML" in prompt
        assert "DOM 注入" in prompt


class TestToutiaoMarkdownPrompt:
    def test_markdown_prompt_requires_rich_html_body_tool(self) -> None:
        prompt = ToutiaoPlatform().get_agent_prompt(
            title="markdown-title",
            content="# Heading\n\nMarkdown body",
            cover_instruction="cover not required",
            body_image_instruction="",
            is_markdown=True,
            rich_html="<p><strong>Heading</strong></p><p>Markdown body</p>",
        )

        assert "paste_rich_html_body" in prompt
        assert "ClipboardItem" not in prompt
        assert "insertHTML" not in prompt


class TestSohuPrompt:
    def test_plain_text_prompt_requires_plain_body_tool(self) -> None:
        prompt = SohuPlatform().get_agent_prompt(
            title="plain-title",
            content="plain body for probe",
            cover_instruction="cover not required",
            body_image_instruction="",
            is_markdown=False,
        )

        assert "paste_plain_text_body" in prompt
        assert "navigator.clipboard.writeText" not in prompt
        assert "```javascript" not in prompt
        assert "工具返回 ok=false，最多重新聚焦编辑器后再调用 1 次；仍失败时再使用 input/type 方式写入正文" not in prompt

    def test_markdown_prompt_requires_rich_html_body_tool(self) -> None:
        prompt = SohuPlatform().get_agent_prompt(
            title="markdown-title",
            content="# Heading\n\nMarkdown body",
            cover_instruction="cover not required",
            body_image_instruction="",
            is_markdown=True,
            rich_html="<p><strong>Heading</strong></p><p>Markdown body</p>",
        )

        assert "paste_rich_html_body" in prompt
        assert "ClipboardItem" not in prompt
        assert "```javascript" not in prompt

    def test_prompt_requires_sohu_cover_tool_and_forbids_manual_cover_clicks(self) -> None:
        prompt = SohuPlatform().get_agent_prompt(
            title="cover-title",
            content="plain body for probe",
            cover_instruction="cover not required",
            body_image_instruction="",
            is_markdown=False,
        )

        assert "set_sohu_cover" in prompt
        assert "不要手动点击正文图片、素材库、本地上传或确定按钮" in prompt
        assert "封面工具返回 ok=false 时最多重试 1 次" in prompt


class TestPlatformTitleMatching:
    def test_ignores_internal_whitespace_when_matching_toutiao_article_title(self) -> None:
        assert PlatformConfig.title_matches(
            "把养生过成日常：真正有效的健康001",
            "把养生过成日常：真正有效的健康 001 06-18 10:27 审核中 修改 删除作品",
        )

    def test_ignores_internal_whitespace_when_matching_sohu_article_title(self) -> None:
        assert PlatformConfig.title_matches(
            "把养生过成日常：真正有效的健康001",
            "把养生过成日常：真正有效的健康 001 已发布 预览 修改",
        )

    def test_does_not_match_unrelated_article_title(self) -> None:
        assert not PlatformConfig.title_matches(
            "把养生过成日常：真正有效的健康001",
            "完全不同的文章标题 已发布 预览 修改",
        )


class TestSohuHasLoginCookie:
    """回归：搜狐登录页一加载就种匿名 cookie（SUV），不能被当成已登录，
    否则刚打开登录页、还没登录就被自动绑定（channel 直接变 bound）。"""

    def test_anonymous_cookie_is_not_logged_in(self) -> None:
        # SUV 是匿名访客标识，登录前就存在，绝不能判为已登录
        anon = [{"name": "SUV", "value": "abc", "domain": ".sohu.com"}]
        assert SohuPlatform().has_login_cookie(anon) is False

    def test_passport_cookie_is_logged_in(self) -> None:
        # ppinf/ppmdig 由 passport 在登录成功后下发
        for name in ("ppinf", "ppmdig"):
            cookies = [{"name": name, "value": "xyz", "domain": ".sohu.com"}]
            assert SohuPlatform().has_login_cookie(cookies) is True

    def test_native_key_still_falls_back_to_suv(self) -> None:
        # 登录判定收紧，但去重键仍可回退到 SUV，互不影响
        anon = [{"name": "SUV", "value": "abc", "domain": ".sohu.com"}]
        assert SohuPlatform().extract_native_key(anon) == "abc"


class TestBodyProbe:
    def test_strips_markdown_and_compacts(self) -> None:
        # 图片/链接/标题/强调符号被去掉，空白被压缩，截取前 30 字符
        probe = PlatformConfig.body_probe("# 标题\n\n这是**正文** [链接](http://x) ![图](http://y)")
        assert probe == "标题这是正文链接"

    def test_empty(self) -> None:
        assert PlatformConfig.body_probe("") == ""
        assert PlatformConfig.body_probe(None) == ""

    def test_truncates_to_30(self) -> None:
        assert len(PlatformConfig.body_probe("一" * 100)) == 30

    def test_matches_sohu_prompt_probe(self) -> None:
        # 内核与搜狐 prompt 必须共用同一算法，两边探针一致
        content = "# 标题\n\n正文内容 *斜体* 与 [链接](http://x)"
        plain = PlatformConfig.strip_markdown(content)
        assert PlatformConfig.body_probe(content) == "".join(plain.split())[:30]


class TestBaseHelpers:
    def _cfg(self) -> PlatformConfig:
        return PlatformConfig(
            name="demo",
            home_url="https://demo.example",
            publish_url="https://demo.example/publish",
            auth_domains=["demo.example"],
            account_cookies=["acc_name"],
        )

    def test_validate_cookies_match(self) -> None:
        cfg = self._cfg()
        assert cfg.validate_cookies([{"domain": "www.demo.example"}]) is True

    def test_validate_cookies_reverse_containment(self) -> None:
        # base 实现是双向包含：domain in auth_domain 或 auth_domain in domain
        cfg = self._cfg()
        assert cfg.validate_cookies([{"domain": "demo"}]) is True

    def test_validate_cookies_no_match(self) -> None:
        cfg = self._cfg()
        assert cfg.validate_cookies([{"domain": "other.com"}]) is False

    def test_validate_cookies_empty(self) -> None:
        assert self._cfg().validate_cookies([]) is False

    def test_extract_account_name(self) -> None:
        cfg = self._cfg()
        cookies = [
            {"name": "irrelevant", "value": "x"},
            {"name": "acc_name", "value": "我的账号"},
        ]
        assert cfg.extract_account_name(cookies) == "我的账号"

    def test_extract_account_name_missing(self) -> None:
        cfg = self._cfg()
        assert cfg.extract_account_name([{"name": "irrelevant", "value": "x"}]) == ""
