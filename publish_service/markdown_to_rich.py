"""Utilities for converting Markdown into rich-text HTML."""

from __future__ import annotations

import html
import re
import sys

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


def markdown_to_html(markdown_text: str) -> str:
    """Convert Markdown into HTML that can be pasted into a rich-text editor."""
    if not markdown_text:
        return ""

    try:
        import markdown

        try:
            html_content = markdown.markdown(
                markdown_text,
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
                markdown_text,
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
        html_content = _simple_markdown_convert(markdown_text)

    # Toutiao's editor tends to preserve <i> more reliably than <em>, and
    # simple heading downgrades avoid editors that do not accept h1/h2 tags.
    html_content = html_content.replace("<em>", "<i>").replace("</em>", "</i>")
    html_content = re.sub(r"<h1>(.*?)</h1>", r"<p><strong>\1</strong></p>", html_content, flags=re.DOTALL)
    html_content = re.sub(r"<h2>(.*?)</h2>", r"<p><strong>\1</strong></p>", html_content, flags=re.DOTALL)

    return html_content


def _simple_markdown_convert(text: str) -> str:
    """Fallback Markdown-to-HTML conversion for environments without markdown."""
    result = html.escape(text)

    for pattern, replacement in MARKDOWN_RULES:
        result = re.sub(pattern, replacement, result)

    result = result.replace("\n", "<br>\n")
    return result


def copy_to_clipboard(html_content: str) -> bool:
    """Copy HTML content into the system clipboard when local helpers exist."""
    try:
        import pyperclip

        pyperclip.copy(html_content)

        pure_text = re.sub(r"<[^>]+>", "", html_content).replace("<br>", "\n")

        try:
            import win32clipboard
            import win32con

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                html_payload = _create_html_format(html_content)
                win32clipboard.SetClipboardData(win32con.CF_HTML, html_payload)
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, pure_text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except ImportError:
            return True
    except ImportError:
        print("error: pyperclip is not installed", file=sys.stderr)
        return False
    except Exception as exc:  # pragma: no cover - defensive clipboard wrapper
        print(f"copy to clipboard failed: {exc}", file=sys.stderr)
        return False


def _create_html_format(html_content: str) -> bytes:
    """Build a CF_HTML payload for the Windows clipboard."""
    header_html = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    header_prefix = "<html><body><!--StartFragment-->"
    header_suffix = "<!--EndFragment--></body></html>"

    html_fragment = f"<!--StartFragment-->{html_content}<!--EndFragment-->"

    start_html = len(header_html.format(0, 0, 0, 0))
    start_fragment = start_html + len(header_prefix)
    end_fragment = start_fragment + len(html_fragment.encode("utf-8"))
    end_html = end_fragment + len(header_suffix)

    html_document = (
        header_html.format(start_html, end_html, start_fragment, end_fragment)
        + header_prefix
        + html_fragment
        + header_suffix
    )

    return html_document.encode("utf-8")


def markdown_to_clipboard(markdown_text: str) -> bool:
    """Convert Markdown to HTML and copy it to the clipboard."""
    html_content = markdown_to_html(markdown_text)
    return copy_to_clipboard(html_content)


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


if __name__ == "__main__":
    test_md = """# Rich Text Test

This is a **bold** and *italic* example.

[Baidu](https://www.baidu.com)

![Image](https://example.com/image.png)
"""

    html_result = markdown_to_html(test_md)
    print(html_result)
