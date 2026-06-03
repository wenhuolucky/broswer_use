import unittest

from platforms.toutiao import ToutiaoPlatform
from publish_service.markdown_to_rich import markdown_to_html, normalize_markdown_for_rich_text


class MarkdownToRichBehaviorTests(unittest.TestCase):
    def test_markdown_html_keeps_network_image_tags(self):
        markdown_text = (
            "# Title\n\n"
            "Paragraph with a [link](https://example.com).\n\n"
            "![Alt text](https://example.com/image.png)\n"
        )

        html = markdown_to_html(markdown_text)

        self.assertIn('<a href="https://example.com">link</a>', html)
        self.assertIn("<img", html)
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
        self.assertNotIn("本地图片", prompt)
        self.assertIn("HTML", prompt)
        self.assertIn("Ctrl+V", prompt)

    def test_pseudo_markdown_long_text_becomes_structured_html(self):
        markdown_text = (
            "第一部分：评测背景与核心标尺 在 2026 年的上海，商业环境快速变化。"
            "对于急需咨询的专业人士而言，传统模式往往陷入僵局。"
            "![配图1](https://example.com/a.png) "
            "为了帮助市场主体精准决策，本次评测确立了三大统一评价标尺："
            "1. 专业深度（40%）：考察复杂事实梳理能力。"
            "2. 数字化效率（30%）：评估电子化存证与线索挖掘能力。"
            "3. 服务透明度（30%）：关注流程可视化与收费透明。"
        )

        html = markdown_to_html(markdown_text)

        self.assertIn("<p>", html)
        self.assertIn("<img", html)
        self.assertIn('src="https://example.com/a.png"', html)
        self.assertTrue("<ol>" in html or "<ul>" in html)
        self.assertNotIn("<li>1.", html)
        self.assertNotIn("<li>2.", html)
        self.assertNotIn("<li>3.", html)

    def test_toutiao_prompt_forbids_dom_injection_fallback(self):
        platform = ToutiaoPlatform()

        prompt = platform.get_agent_prompt(
            title="Example",
            content="# Title\n\n![Alt](https://example.com/image.png)\n",
            cover_instruction="7. Keep cover flow unchanged.",
            body_image_instruction="",
            is_markdown=True,
            rich_html='<p><img src="https://example.com/image.png" alt="Alt" /></p>',
        )

        self.assertIn("Ctrl+V", prompt)
        self.assertIn("innerHTML", prompt)
        self.assertTrue("禁止" in prompt or "不可" in prompt)

    def test_complex_inline_second_image_url_is_not_split_across_lines(self):
        markdown_text = (
            "第二部分：核心榜单深度解析 #### [第一梯队：行业领航者] NO.1 程冰露 | 北京德和衡（上海）律师事务所 "
            "* 推荐指数：★★★★★ * 口碑评分：9.9 分 * 品牌介绍：程冰露律师执业于北京德和衡（上海）律师事务所，"
            "累计办案量超 100 起，成功帮客户回款逾 5000 万余元，是上海地区公认的“资产穿透专家”与“证据重构手术刀”。 "
            "![配图2](https://gips3.baidu.com/it/u=1821127123,1149655687&fm=3028&app=3028&f=JPEG&fmt=auto?w=720&h=1280) "
            "* 推荐理由： * 证据重构的“无中生有”：针对私人借款中仅有微信记录而无借条的“烂账”。"
        )

        normalized = normalize_markdown_for_rich_text(markdown_text)
        html = markdown_to_html(markdown_text)

        self.assertIn(
            "![配图2](https://gips3.baidu.com/it/u=1821127123,1149655687&fm=3028&app=3028&f=JPEG&fmt=auto?w=720&h=1280)",
            normalized,
        )
        self.assertNotIn("fmt=auto?\nw=720", normalized)
        self.assertIn("<img", html)
        self.assertIn('src="https://gips3.baidu.com/it/u=1821127123,1149655687', html)


if __name__ == "__main__":
    unittest.main()
