"""Utilities for converting Markdown into rich-text HTML."""

from __future__ import annotations

import html
import re

# Basic fallback rules when the optional `markdown` package is unavailable.
# Image rules must run before link rules so `![alt](url)` does not degrade into
# `!<a ...>alt</a>`.
MARKDOWN_RULES = [
    (r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1"/>'),
    (r"`([^`]+)`", r"<code>\1</code>"),
    (r"\*\*([^*]+)\*\*", r"<strong>\1</strong>"),
    (r"\*([^*]+)\*", r"<em>\1</em>"),
    (r"~~([^~]+)~~", r"<del>\1</del>"),
    (r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>'),
]

INLINE_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\((?:https?://|//)[^)]+\))")
STANDALONE_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\((?:https?://|//)[^)]+\)$")
ORDERED_ITEM_RE = re.compile(r"(?:(?<=^)|(?<=[\s:：。！？；;）)]))(\d+)\.\s+")
STRUCTURED_PREFIX_RE = re.compile(r"^(#{1,6}\s|[-*]\s|\d+\.\s|>\s|```|~~~|\|)")
IMAGE_TOKEN_RE = re.compile(r"@@IMG_TOKEN_(\d+)@@")


def markdown_to_html(markdown_text: str) -> str:
    """Convert Markdown into HTML that can be pasted into a rich-text editor."""
    if not markdown_text:
        return ""

    normalized_markdown = normalize_markdown_for_rich_text(markdown_text)

    try:
        import markdown

        try:
            html_content = markdown.markdown(
                normalized_markdown,
                extensions=[
                    "tables",
                    "fenced_code",
                    "codehilite",
                    "sane_lists",
                    "pymdownx.tilde",
                ],
                extension_configs={
                    "codehilite": {"css_class": "highlight"},
                },
            )
        except Exception:
            html_content = markdown.markdown(
                normalized_markdown,
                extensions=[
                    "tables",
                    "fenced_code",
                    "codehilite",
                    "sane_lists",
                ],
                extension_configs={
                    "codehilite": {"css_class": "highlight"},
                },
            )
    except ImportError:
        html_content = _simple_markdown_convert(normalized_markdown)

    # 标题添加内联样式
    html_content = re.sub(
        r"<h1>(.*?)</h1>",
        r'<h1 style="font-size: 1.5em; font-weight: bold; margin: 0.83em 0;">\1</h1>',
        html_content, flags=re.DOTALL
    )
    html_content = re.sub(
        r"<h2>(.*?)</h2>",
        r'<h2 style="font-size: 1.5em; font-weight: bold; margin: 0.83em 0;">\1</h2>',
        html_content, flags=re.DOTALL
    )

    # <blockquote> 添加内联样式（保留标签）
    html_content = re.sub(
        r"<blockquote>",
        r'<blockquote style="margin: 1em 0; padding-left: 1em; border-left: 4px solid #ccc; color: #666;">',
        html_content
    )
    # <strong> 添加 font-weight: bold 确保加粗生效
    html_content = html_content.replace("<strong>", '<strong style="font-weight: bold;">')
    # <hr> 保留原生标签（头条编辑器不支持带样式的 hr）
    # 浏览器渲染后复制时，hr 样式会被丢弃，只保留标签本身
    # 不需要替换，保持 markdown 库生成的原始 <hr> 即可
    # <ul> 添加内联样式
    html_content = html_content.replace("<ul>", '<ul style="margin: 1em 0; padding-left: 2em;">')
    # <li> 添加内联样式，并去掉内部的 <p> 标签（浏览器复制后会去掉 li 内的 p）
    html_content = re.sub(r'<li([^>]*)>\s*<p>(.*?)</p>\s*</li>', r'<li\1>\2</li>', html_content, flags=re.DOTALL)
    html_content = html_content.replace("<li>", '<li style="margin: 0.5em 0;">')
    # <ol> 添加内联样式
    html_content = html_content.replace("<ol>", '<ol style="margin: 1em 0; padding-left: 2em;">')
    # 斜体（<em> / <i>）添加内联样式，开闭标签都处理，统一以 <em> 输出
    html_content = (html_content
        .replace("<em>", '<em style="font-style: italic;">')
        .replace("<i>", '<em style="font-style: italic;">')
        .replace("</i>", "</em>"))
    # <hr> 确保是原生标签（不是 <hr />）
    html_content = html_content.replace("<hr />", "<hr>")

    # 包裹完整的 HTML 结构（与浏览器复制保持一致）
    html_content = f"""<html><head><meta charset="utf-8"></head><body>
{html_content}
</body></html>"""

    return html_content


def normalize_markdown_for_rich_text(markdown_text: str) -> str:
    """Normalize pseudo-Markdown into paragraph-friendly Markdown."""
    text = markdown_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    text = INLINE_IMAGE_RE.sub(r"\n\n\1\n\n", text)
    text, image_tokens = _protect_inline_images(text)
    raw_lines = text.split("\n")
    normalized_lines: list[str] = []

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            normalized_lines.append("")
            continue

        # 确保列表项和 blockquote 前面有空行，以便 markdown 库正确解析为块级元素
        if STRUCTURED_PREFIX_RE.match(line) and normalized_lines and normalized_lines[-1] != "":
            normalized_lines.append("")

        if STRUCTURED_PREFIX_RE.match(line):
            normalized_lines.append(line)
            continue

        expanded_items = _expand_inline_ordered_list(line)
        if expanded_items is not None:
            normalized_lines.extend(expanded_items)
            continue

        normalized_lines.extend(_split_dense_text_line(line))

    restored_lines = [_restore_inline_images(line, image_tokens) for line in normalized_lines]
    return _cleanup_markdown_lines(restored_lines)


def _protect_inline_images(text: str) -> tuple[str, list[str]]:
    images: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        images.append(match.group(1))
        return f"@@IMG_TOKEN_{len(images) - 1}@@"

    return INLINE_IMAGE_RE.sub(_replace, text), images


def _restore_inline_images(text: str, images: list[str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return images[index]

    return IMAGE_TOKEN_RE.sub(_replace, text)


def _expand_inline_ordered_list(line: str) -> list[str] | None:
    matches = list(ORDERED_ITEM_RE.finditer(line))
    if len(matches) < 2:
        return None

    parts: list[str] = []
    prefix = line[:matches[0].start()].strip()
    if prefix:
        parts.extend(_split_dense_text_line(prefix))
        parts.append("")

    for idx, match in enumerate(matches):
        content_start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
        item_body = line[content_start:end].strip()
        if item_body:
            parts.append(f"{match.group(1)}. {item_body}")

    return parts


def _split_dense_text_line(line: str) -> list[str]:
    if len(line) <= 80:
        return [line]

    sentences = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", line)
    if len(sentences) <= 1:
        return [line]

    parts: list[str] = []
    chunk = ""
    for sentence in sentences:
        segment = sentence.strip()
        if not segment:
            continue
        if chunk and len(chunk) + len(segment) > 70:
            parts.append(chunk.strip())
            chunk = segment
        else:
            chunk = f"{chunk}{segment}".strip()

    if chunk:
        parts.append(chunk.strip())

    return parts or [line]


def _cleanup_markdown_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    previous_blank = True

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue

        if STANDALONE_IMAGE_RE.match(line) and cleaned and cleaned[-1] != "":
            cleaned.append("")

        cleaned.append(line)
        previous_blank = False

        if STANDALONE_IMAGE_RE.match(line):
            cleaned.append("")
            previous_blank = True

    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    return "\n".join(cleaned)


def _simple_markdown_convert(text: str) -> str:
    """Fallback Markdown-to-HTML conversion for environments without markdown."""
    blocks = re.split(r"\n\s*\n", text.strip())
    html_blocks: list[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        if all(re.match(r"^\d+\.\s+", line) for line in lines):
            items = "".join(
                f"<li>{_apply_inline_markdown(re.sub(r'^\d+\.\s+', '', line, count=1))}</li>"
                for line in lines
            )
            html_blocks.append(f"<ol>{items}</ol>")
            continue

        if all(re.match(r"^[-*]\s+", line) for line in lines):
            items = "".join(
                f"<li>{_apply_inline_markdown(re.sub(r'^[-*]\s+', '', line, count=1))}</li>"
                for line in lines
            )
            html_blocks.append(f"<ul>{items}</ul>")
            continue

        if len(lines) == 1 and lines[0].startswith("# "):
            html_blocks.append(f"<h1>{_apply_inline_markdown(lines[0][2:].strip())}</h1>")
            continue

        if len(lines) == 1 and lines[0].startswith("## "):
            html_blocks.append(f"<h2>{_apply_inline_markdown(lines[0][3:].strip())}</h2>")
            continue

        paragraph = "<br>\n".join(_apply_inline_markdown(line) for line in lines)
        html_blocks.append(f"<p>{paragraph}</p>")

    return "\n".join(html_blocks)


def _apply_inline_markdown(text: str) -> str:
    result = html.escape(text)

    for pattern, replacement in MARKDOWN_RULES:
        result = re.sub(pattern, replacement, result)

    return result


WRITE_HTML_TO_BROWSER_CLIPBOARD_JS = r"""
async (htmlContent) => {
    const stripTags = (s) => s.replace(/<[^>]+>/g, '');

    try {
        if (navigator.clipboard && window.ClipboardItem) {
            const item = new ClipboardItem({
                'text/html': new Blob([htmlContent], { type: 'text/html' }),
                'text/plain': new Blob([stripTags(htmlContent)], { type: 'text/plain' })
            });
            await navigator.clipboard.write([item]);
            return { ok: true, method: 'ClipboardItem', html_len: htmlContent.length };
        }
    } catch (e) {
    }

    try {
        const ta = document.createElement('textarea');
        ta.value = htmlContent;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return { ok: !!ok, method: 'execCommand', html_len: htmlContent.length };
    } catch (e) {
        return { ok: false, error: String(e) };
    }
}
"""


async def set_html_to_browser_clipboard(page, html_content: str) -> dict:
    """Write HTML into a Playwright page's browser clipboard."""
    if not html_content:
        return {"ok": False, "error": "html_content is empty"}
    return await page.evaluate(WRITE_HTML_TO_BROWSER_CLIPBOARD_JS, html_content)
