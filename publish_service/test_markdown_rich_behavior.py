import unittest

from platforms.toutiao import ToutiaoPlatform
from publish_service.markdown_to_rich import markdown_to_html


class MarkdownToRichBehaviorTests(unittest.TestCase):
    def test_markdown_html_keeps_network_image_tags(self):
        markdown_text = (
            "# Title\n\n"
            "Paragraph with a [link](https://example.com).\n\n"
            "![Alt text](https://example.com/image.png)\n"
        )

        html = markdown_to_html(markdown_text)

        self.assertIn('<a href="https://example.com">link</a>', html)
        self.assertIn('<img', html)
        self.assertIn('src="https://example.com/image.png"', html)

    def test_toutiao_prompt_no_longer_mentions_body_image_upload(self):
        platform = ToutiaoPlatform()

        prompt = platform.get_agent_prompt(
            title="Example",
            content="# Title\n\n![Alt](https://example.com/image.png)\n",
            cover_instruction="7. Keep cover flow unchanged.",
            body_image_instruction="",
            is_markdown=True,
            rich_html='<p><img src="https://example.com/image.png" alt="Alt" /></p>',
        )

        self.assertNotIn("body_image_paths", prompt)
        self.assertNotIn("工具栏", prompt)
        self.assertNotIn("本地图片", prompt)
        self.assertIn("HTML", prompt)
        self.assertIn("Ctrl+V", prompt)


if __name__ == "__main__":
    unittest.main()
