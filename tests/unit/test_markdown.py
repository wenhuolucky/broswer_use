"""Markdown → HTML 测试（app/utils/markdown.py）。

两条路径：装了 markdown 包走库转换，否则走手写 _simple_markdown_convert。
本机当前未安装 markdown 包，但测试不依赖这一点——
fallback 相关断言统一用 monkeypatch 强制走 fallback，避免日后装包后断言漂移。
"""

from __future__ import annotations

import sys

import pytest

from app.utils import markdown as md


@pytest.fixture
def force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 `import markdown` 抛 ImportError，强制 markdown_to_html 走 fallback。"""
    monkeypatch.setitem(sys.modules, "markdown", None)


class TestMarkdownToHtml:
    def test_empty_returns_empty(self) -> None:
        assert md.markdown_to_html("") == ""

    def test_heading_kept_with_inline_style(self, force_fallback: None) -> None:
        # h1/h2 保留原生标签 + 内联样式，不再降级为 <p><strong>
        out = md.markdown_to_html("# 标题")
        assert "font-size: 1.5em" in out
        assert "标题</h1>" in out

    def test_em_kept_with_inline_style(self, force_fallback: None) -> None:
        # <em> 保留 + 内联样式，不再替换为 <i>
        out = md.markdown_to_html("这是 *斜体* 文字")
        assert '<em style="font-style: italic;">斜体</em>' in out

    def test_bold_with_inline_style(self, force_fallback: None) -> None:
        # <strong> 添加 font-weight: bold
        out = md.markdown_to_html("**加粗**")
        assert '<strong style="font-weight: bold;">加粗</strong>' in out


class TestSimpleConvert:
    """直接测 fallback 实现，钉死各 block 类型。"""

    def test_ordered_list(self) -> None:
        out = md._simple_markdown_convert("1. 一\n2. 二")
        assert out == "<ol><li>一</li><li>二</li></ol>"

    def test_unordered_list(self) -> None:
        out = md._simple_markdown_convert("- a\n- b")
        assert out == "<ul><li>a</li><li>b</li></ul>"

    def test_h1_h2(self) -> None:
        assert md._simple_markdown_convert("# Title") == "<h1>Title</h1>"
        assert md._simple_markdown_convert("## Sub") == "<h2>Sub</h2>"

    def test_paragraph_with_inline(self) -> None:
        out = md._simple_markdown_convert("普通 **粗** 文本")
        assert out == "<p>普通 <strong>粗</strong> 文本</p>"

    def test_html_escaped(self) -> None:
        # 内联转换前先 html.escape，防止注入
        out = md._simple_markdown_convert("a < b & c")
        assert "&lt;" in out and "&amp;" in out

    def test_image_before_link_rule(self) -> None:
        # 图片规则必须先于链接规则，避免 ![alt](url) 退化成 !<a>…</a>
        out = md._simple_markdown_convert("![图](https://x.com/i.png)")
        assert '<img src="https://x.com/i.png" alt="图"/>' in out
        assert "<a " not in out


class TestNormalizeForRichText:
    def test_empty(self) -> None:
        assert md.normalize_markdown_for_rich_text("") == ""
        assert md.normalize_markdown_for_rich_text("   ") == ""

    def test_crlf_normalized(self) -> None:
        out = md.normalize_markdown_for_rich_text("a\r\nb")
        assert "\r" not in out

    def test_inline_ordered_list_expanded(self) -> None:
        # 单行内的 "1. x 2. y" 被拆成多行列表项
        out = md.normalize_markdown_for_rich_text("步骤：1. 烧水 2. 泡茶")
        lines = out.split("\n")
        assert "1. 烧水" in lines
        assert "2. 泡茶" in lines

    def test_standalone_image_gets_blank_lines(self) -> None:
        # 独立成行的图片前后补空行，保证后续转成独立 <p><img></p>
        out = md.normalize_markdown_for_rich_text(
            "文字\n![图](https://x.com/i.png)\n更多文字"
        )
        assert "![图](https://x.com/i.png)" in out
