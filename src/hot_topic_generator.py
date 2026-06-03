#!/usr/bin/env python3
"""
热点文章自动生成模块
从头条热榜检测热点话题，通过 LLM 两轮生成（草稿 + 人性化）输出适合头条号发布的文章。

用法:
  python hot_topic_generator.py --list-topics          # 列出当前热点
  python hot_topic_generator.py --generate             # 生成文章（自动选 TOP1）
  python hot_topic_generator.py --generate --topic-index 3  # 指定话题
  python hot_topic_generator.py --auto-publish         # 生成并自动发布到头条号
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import httpx

# ============================================================
# 封面图片生成
# ============================================================

_MINIMAX_IMAGE_API = "https://api.minimaxi.com/v1/image_generation"
_DEFAULT_IMAGE_SIZE = (1024, 768)  # 头条号封面推荐比例 4:3


def _get_minimax_api_key() -> Optional[str]:
    """获取 MiniMax API Key"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return os.getenv("MINIMAX_API_KEY")


async def generate_cover_image(topic_title: str, article_title: str,
                                output_dir: Optional[Path] = None) -> Optional[str]:
    """
    根据热点话题生成封面图片，保存到指定目录。

    Args:
        topic_title: 热点话题标题（用于生成图片描述）
        article_title: 最终文章标题
        output_dir: 图片保存目录（默认与脚本同级的 test_perper/{序号}/）

    Returns:
        保存的图片文件路径，失败返回 None
    """
    api_key = _get_minimax_api_key()
    if not api_key:
        print("  ⚠ 未配置 MINIMAX_API_KEY，跳过封面生成")
        return None

    # 第一轮：用 LLM 将中文话题转为英文图片描述
    prompt_desc = await _topic_to_image_prompt(topic_title)
    if not prompt_desc:
        # 降级：直接用话题标题
        prompt_desc = f"editorial photography about {topic_title}, realistic style, no text"

    print(f"  封面描述: {prompt_desc}")

    # 第二轮：调用 MiniMax 生成图片
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                _MINIMAX_IMAGE_API,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "image-01",
                    "prompt": prompt_desc,
                },
            )
            if resp.status_code != 200:
                print(f"  ⚠ 封面生成失败: HTTP {resp.status_code}")
                return None
            data = resp.json()
            urls = data.get("data", {}).get("image_urls", [])
            if not urls:
                status_msg = data.get("base_resp", {}).get("status_msg", "unknown")
                print(f"  ⚠ 封面生成失败: {status_msg}")
                return None

            img_url = urls[0]

        # 下载图片
        async with httpx.AsyncClient(timeout=30.0) as client:
            img_resp = await client.get(img_url)
            img_data = img_resp.content

        # 保存图片
        if output_dir is not None:
            # 直接使用传入的目录
            target_dir = Path(output_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            # 自动找可用的序号目录
            output_dir = ARTICLE_DIR
            idx = 1
            while True:
                dir_name = f"perper{idx:03d}"
                target_dir = output_dir / dir_name
                if target_dir.exists() and (target_dir / "article.txt").exists():
                    break
                idx += 1
                if idx > 9999:
                    target_dir = output_dir / "article_new"
                    target_dir.mkdir(parents=True, exist_ok=True)
                    break

        cover_path = target_dir / "cover.jpg"
        with open(cover_path, "wb") as f:
            f.write(img_data)

        print(f"  封面已保存: {cover_path} ({len(img_data) / 1024:.0f} KB)")
        return str(cover_path)

    except Exception as e:
        print(f"  ⚠ 封面生成异常: {e}")
        return None


async def _topic_to_image_prompt(topic_title: str) -> Optional[str]:
    """用 LLM 将中文话题转为英文图片生成描述"""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from publisher import get_browser_llm
        llm = get_browser_llm()
    except Exception:
        return None

    from browser_use.llm import SystemMessage, UserMessage

    system_prompt = """你是一个图片描述生成器。根据给定的中文话题，生成一段英文图片生成提示词（prompt）。
要求：
- 描述一个与话题相关的场景，写实摄影风格
- 画面必须明亮、光线充足，色彩鲜艳，适合用作文章封面
- 禁止使用 dimly lit, dark, gloomy, melancholic, moody, shadowy 等暗色调词汇
- 强调积极、温暖的氛围
- 不要包含任何文字、字幕、标语
- 不要出现政治人物或名人
- 输出格式：只返回英文描述，不要额外解释

示例：
话题：中华田园犬——中华民族最忠实的伙伴
描述：A loyal Chinese rural dog sitting on a sunny dirt path in a peaceful village, warm golden sunlight, bright natural photography, shallow depth of field, vibrant colors, inviting atmosphere"""

    user_prompt = f"话题：{topic_title}\n描述："

    messages = [
        SystemMessage(content=system_prompt),
        UserMessage(content=user_prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw = response.completion
        desc = str(raw).strip() if raw else ""

        # 后处理：强制移除暗色调词汇
        dark_words = ["dimly lit", "dark", "gloomy", "melancholic", "moody", "shadowy",
                       "overcast", "somber", "dull", "murky", "shadows"]
        for w in dark_words:
            desc = desc.replace(w, "bright")
        # 强制添加明亮光线索引
        if not any(w in desc.lower() for w in ["sunlight", "bright", "warm light", "natural light"]):
            desc += ", bright natural lighting, vibrant colors"

        return desc[:300]
    except Exception as e:
        print(f"  ⚠ 图片描述生成失败: {e}")
        return None


# ============================================================
# 本地文章库 — 重复话题检测
# ============================================================

ARTICLE_DIR = Path(__file__).parent / "test_perper"


def _sanitize_filename(name: str, max_len: int = 40) -> str:
    """清理文件名非法字符，截断过长名称"""
    bad_chars = r'<>:"/\|?*'
    for c in bad_chars:
        name = name.replace(c, "")
    name = name.strip()
    if len(name) > max_len:
        name = name[:max_len]
    if not name:
        name = "untitled"
    return name


TOPICS_USED_FILE = ARTICLE_DIR / "topics_used.json"


def load_used_topics() -> list[dict]:
    """加载已使用的原始热点话题记录，返回 [{"topic_title": ..., "article_title": ..., "time": ...}, ...]"""
    if not TOPICS_USED_FILE.exists():
        return []
    try:
        data = json.loads(TOPICS_USED_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_used_topic(topic_title: str, article_title: str):
    """记录已使用的热点话题，用于后续去重。"""
    used = load_used_topics()
    used.append({
        "topic_title": topic_title,
        "article_title": article_title,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    TOPICS_USED_FILE.write_text(json.dumps(used, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_articles() -> list[dict]:
    """
    从 test_perper/ 目录扫描已有文章，返回 (标题, 正文, 文件路径) 列表。
    支持 article.txt / article.md / .txt / .md 文件。
    """
    articles = []
    if not ARTICLE_DIR.exists():
        return articles

    for subdir in sorted(ARTICLE_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        # 优先读取 article.txt 或 article.md
        for fname in ("article.txt", "article.md"):
            fpath = subdir / fname
            if fpath.exists():
                text = fpath.read_text(encoding="utf-8", errors="ignore").strip()
                if not text:
                    continue
                # 提取标题：第一行（如果是 markdown 则去掉 # 前缀）
                lines = text.split("\n")
                title = lines[0].lstrip("#").strip() if lines else subdir.name
                articles.append({"title": title, "content": text, "path": str(fpath)})
                break
    return articles


def is_topic_duplicate(topic_title: str, threshold: float = 0.5) -> Tuple[bool, Optional[str]]:
    """
    判断热点话题是否与已有文章重复。

    检测逻辑（按优先级）：
    1. 精确匹配：原始热点标题是否已在 topics_used.json 中记录过
    2. 模糊匹配：新话题与已记录话题的关键词重叠度
    3. 兜底：话题标题是否出现在已有文章标题/正文中

    Args:
        topic_title: 热点话题标题
        threshold: 关键词重叠阈值（0-1），默认 0.5

    Returns:
        (是否重复, 匹配到的已有文章标题)
    """
    # 第一层：检查原始热点标题是否精确重复
    used = load_used_topics()
    for u in used:
        if u["topic_title"].strip() == topic_title.strip():
            return True, u.get("article_title", u["topic_title"])

    # 第二层：关键词模糊匹配已使用过的热点
    def tokenize(text: str) -> set:
        """简单分词：提取 2 字以上的中文词 + 英文单词，过滤停用词"""
        stop_words = {"的", "了", "是", "在", "有", "和", "与", "或", "不", "这", "那", "什么", "为什么",
                      "一个", "一些", "这个", "那个", "如何", "为什么", "怎么", "如果"}
        words = set()
        for w in re.findall(r'[a-zA-Z]+', text):
            words.add(w.lower())
        chinese_parts = re.findall(r'[一-鿿]{2,}', text)
        for part in chinese_parts:
            for i in range(len(part) - 1):
                w2 = part[i:i+2]
                if w2 not in stop_words:
                    words.add(w2)
                if i < len(part) - 2:
                    words.add(part[i:i+3])
        return words

    topic_words = tokenize(topic_title)
    if topic_words:
        for u in used:
            used_words = tokenize(u["topic_title"])
            if used_words:
                overlap = topic_words & used_words
                if len(overlap) / max(len(topic_words), 1) >= threshold:
                    return True, u.get("article_title", u["topic_title"])

    # 第三层：兜底检查文章内容
    existing = load_existing_articles()
    if not existing:
        return False, None

    if not topic_words:
        for art in existing:
            if topic_title in art["title"] or topic_title in art["content"]:
                return True, art["title"]
        return False, None

    for art in existing:
        art_title_words = tokenize(art["title"])
        art_content_words = tokenize(art["content"])
        # 与标题的重叠度
        overlap_title = topic_words & art_title_words
        # 与正文的重叠度
        overlap_content = topic_words & art_content_words

        # 如果话题关键词有 threshold 以上出现在已有文章中，视为重复
        if overlap_title and len(overlap_title) / max(len(topic_words), 1) >= threshold:
            return True, art["title"]
        if overlap_content and len(overlap_content) / max(len(topic_words), 1) >= threshold:
            return True, art["title"]

        # 子串兜底
        if topic_title in art["title"] or topic_title in art["content"]:
            return True, art["title"]

    return False, None


def save_article_to_test_perper(title: str, content: str) -> str:
    """
    将生成的文章保存到 test_perper/{文章名}/article.txt
    文件名即为文章标题（清理后的版本）。

    Returns:
        保存目录路径
    """
    safe_name = _sanitize_filename(title)
    # 创建子目录
    idx = 1
    while True:
        dir_name = f"perper{idx:03d}" if idx <= 999 else f"article_{idx}"
        target_dir = ARTICLE_DIR / dir_name
        if not target_dir.exists():
            break
        idx += 1

    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / "article.txt"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"{title}\n\n{content}")

    print(f"\n文章已保存到 test_perper: {output_path}")
    return str(target_dir)


# ============================================================
# 敏感话题过滤
# ============================================================

# 敏感关键词列表：涉及国家/政治/军事/外交等不宜自媒体的话题
_SENSITIVE_KEYWORDS = [
    # 政治/外交/军事
    "间谍", "外交", "军事", "国防", "军队", "导弹", "轰炸", "谈判", "主权",
    "台海", "南海", "中美", "中俄", "中俄关系", "中日", "领土",
    "巴以", "以巴", "加沙", "以色列", "巴勒斯坦", "冲突", "战争",
    # 国家机构/政府
    "国务院", "外交部", "国防部", "国安", "纪委", "人大", "政协",
    "国台办", "台办", "中联办", "港澳办", "发改委", "公安部",
    # 敏感社会事件
    "暴乱", "抗议", "游行", "示威", "群体事件", "上访", "刑拘", "死刑",
    # 伤亡/灾难（过于血腥沉重的话题不适合自媒体发文）
    "死亡", "身亡", "遇难", "伤亡", "杀害", "屠杀", "坠机", "爆炸",
    # 其他不宜碰的
    "枪决", "贪污", "腐败", "落马", "审查", "监视", "情报",
]


def is_sensitive_by_keywords(title: str) -> bool:
    """通过关键词判断话题是否敏感"""
    for kw in _SENSITIVE_KEYWORDS:
        if kw in title:
            return True
    return False


# ============================================================
# 热点话题抓取
# ============================================================

_HOT_BOARD_URL = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class HotTopicFetcher:
    """从头条热榜 API 抓取热点话题"""

    async def fetch(self, count: int = 20, llm=None) -> list[dict]:
        """
        抓取头条热榜，返回归一化的话题列表（已过滤敏感话题）。

        Args:
            count: 返回的话题数量
            llm: ChatOpenAILike 实例，用于 LLM 语义过滤。
                 如果为 None，则只做关键词过滤。

        Returns:
            [{"title": str, "hot_value": int, "source": str, "cluster_id": str}, ...]
            按 hot_value 降序排列，已排除敏感话题
        """
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(_HOT_BOARD_URL)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data", [])
        topics = []
        for item in items:
            title = item.get("Title", "").strip()
            if not title:
                continue
            topics.append({
                "title": title,
                "hot_value": int(item.get("HotValue", 0)),
                "source": "toutiao",
                "cluster_id": str(item.get("ClusterId", "")),
            })

        # 按热度降序
        topics.sort(key=lambda x: x["hot_value"], reverse=True)

        # 第一层：关键词硬过滤
        before_kw = len(topics)
        topics = [t for t in topics if not is_sensitive_by_keywords(t["title"])]
        print(f"  关键词过滤：排除 {before_kw - len(topics)} 条敏感话题，剩余 {len(topics)} 条")

        # 第二层：LLM 语义过滤（可选，进一步提高准确性）
        if llm is not None and topics:
            topics = await self._llm_filter(llm, topics)

        return topics[:count]

    async def _llm_filter(self, llm, topics: list[dict]) -> list[dict]:
        """
        使用 LLM 判断话题是否涉及敏感内容（政治/军事/外交/国家安全等）。
        批量处理，一次请求判断所有话题。
        """
        from browser_use.llm import SystemMessage, UserMessage

        topic_list = "\n".join(f"{i}. {t['title']}" for i, t in enumerate(topics))

        system_prompt = """你是一个内容审核助手。请判断以下新闻标题是否涉及敏感话题。
敏感话题包括：政治斗争、军事冲突、外交谈判、国家安全、间谍情报、政府高层、领土争端、社会动荡。
非敏感话题包括：娱乐、体育、生活、科技、财经、文化、社会正能量、民生日常。

注意：只需要判断是否敏感，不需要解释原因。"""

        user_prompt = f"""请判断以下每个话题是否敏感，返回 JSON 数组，每个元素为 {{"safe": true/false}}：

{topic_list}

返回格式示例（与上面话题一一对应）：
[{{"safe": true}}, {{"safe": false}}, {{"safe": true}}]"""

        messages = [
            SystemMessage(content=system_prompt),
            UserMessage(content=user_prompt),
        ]

        try:
            response = await llm.ainvoke(messages)
            raw = response.completion
            raw_text = str(raw) if raw is not None else ""

            # 提取 JSON 数组
            m = re.search(r"\[(.*?)\]", raw_text, re.DOTALL)
            if m:
                results = json.loads(f"[{m.group(1)}]")
                safe_topics = []
                for i, t in enumerate(topics):
                    if i < len(results):
                        r = results[i]
                        if isinstance(r, dict) and r.get("safe", True):
                            safe_topics.append(t)
                        elif isinstance(r, bool) and r:
                            safe_topics.append(t)
                        else:
                            safe_topics.append(t)  # 默认保留
                    else:
                        safe_topics.append(t)  # 超出范围默认保留
                print(f"  LLM 过滤：保留 {len(safe_topics)} 条安全话题")
                return safe_topics
        except Exception as e:
            print(f"  LLM 过滤失败，使用关键词过滤结果: {e}")

        return topics


def format_hot_value(val) -> str:
    """格式化热度数值为可读字符串"""
    try:
        v = int(val)
        if v >= 1000000:
            return f"{v / 1000000:.1f}M"
        if v >= 1000:
            return f"{v / 1000:.1f}K"
        return str(v)
    except (ValueError, TypeError):
        return str(val)


# ============================================================
# 文章生成（两轮 LLM）
# ============================================================

# 第一轮系统 prompt：草稿生成
_DRAFT_SYSTEM_PROMPT = """你是一位经验丰富的头条号作者，擅长撰写社会热点、生活百科、情感观点类文章。
你的文章风格：接地气、有温度、观点鲜明但不偏激，善于用具体例子和细节打动读者。

写作要求：
1. 绝对禁止使用"首先、其次、最后、总而言之、综上所述、由此可见"等模板化连接词
2. 绝对禁止使用"在当今社会、随着...的发展、不容忽视、具有重要意义、众所周知"等 AI 常见开头
3. 多用口语化表达、设问、反问，像朋友聊天一样自然
4. 段落要短（3-5 句为主），避免长段落堆砌
5. 适当使用感叹号和省略号增加情感表达
6. 标题要吸引眼球但不低俗，15-25 字
7. 全文不要出现"AI、人工智能、算法、大模型"等字眼
8. 不要用编号列表（1.2.3. 或 一、二、三）
9. 语气像一个有生活阅历的普通人在朋友圈分享看法"""

# 第一轮用户 prompt 模板
_DRAFT_USER_PROMPT = """请根据以下热点话题，写一篇适合发布在头条号的文章。

热点话题：{topic_title}

要求：
- 标题：15-25 字，有吸引力但不标题党
- 正文：800-1200 字
- 开头用一个具体的场景、故事或问题引入，不要直接陈述话题
- 中间部分分析事件背后的原因、影响、各方观点
- 结尾给出一个温和但有启发的观点，不要说教
- 结尾不要写"总而言之""你怎么看"之类的话
- 注意：正文内容中不要在开头加引号"，不要使用 JSON 不支持的特殊字符

请以 JSON 格式返回，不要包含任何额外文字：
{{"title": "文章标题", "content": "文章正文"}}"""

# 第二轮系统 prompt：人性化润色
_HUMANIZE_SYSTEM_PROMPT = """你是一个文章编辑，专门负责把 AI 生成的文章改得更像真人写的。

改写要求：
1. 把任何看起来像 AI 的连接词换成口语化的过渡，如"话说回来""其实吧""这么说吧""有意思的是"
2. 加入设问句和反问句（例如"你发现没？""这事儿吧，你怎么看？"）
3. 缩短过长的段落，拆分成长短句交替的节奏
4. 加入口语表达："说实话""不知道你们有没有同感""你别说""扯远了"
5. 去掉过于正式的书面用语，换成日常说法
6. 让整体读起来像一个有阅历的中年人在讲故事，而不是新闻报道
7. 保留原文的核心信息和观点，不要改变立场
8. 可以调整段落顺序，让节奏更自然
9. 标题也可以微调，让它更自然、更吸引人

请以 JSON 格式返回，不要包含任何额外文字：
{{"title": "修改后的标题", "content": "修改后的正文"}}"""


class ArticleGenerator:
    """两轮 LLM 文章生成器"""

    def __init__(self, llm=None):
        self.llm = llm  # ChatOpenAILike 实例

    def _get_llm(self):
        """延迟加载 LLM（文章生成使用 LLM_PROVIDER 指定的模型，默认 mimo）"""
        if self.llm is not None:
            return self.llm
        # 从 publisher 模块获取（非浏览器任务，支持 mimo/minimax/deepseek）
        sys.path.insert(0, str(Path(__file__).parent))
        from publisher import get_llm
        self.llm = get_llm()
        return self.llm

    async def generate(self, topic: dict) -> Tuple[str, str]:
        """
        根据热点话题生成文章（两轮：草稿 + 人性化）。

        Args:
            topic: {"title": str, "hot_value": int, ...}

        Returns:
            (title, content) 元组
        """
        llm = self._get_llm()

        # --- 第一轮：草稿 ---
        print(f"第一轮：生成文章草稿...")
        draft_title, draft_content = await self._call_llm(
            llm,
            system_prompt=_DRAFT_SYSTEM_PROMPT,
            user_prompt=_DRAFT_USER_PROMPT.format(topic_title=topic["title"]),
            label="草稿",
        )

        if not draft_content:
            raise RuntimeError("第一轮生成失败，未获取到有效正文")

        print(f"  草稿标题: {draft_title}")
        print(f"  草稿字数: {len(draft_content)}")

        # --- 第二轮：人性化 ---
        print(f"\n第二轮：人性化润色...")
        final_title, final_content = await self._call_llm(
            llm,
            system_prompt=_HUMANIZE_SYSTEM_PROMPT,
            user_prompt=f"请改写以下文章，让它更像真人写的。\n\n原标题：{draft_title}\n\n原文：\n{draft_content}",
            label="润色",
        )

        if not final_content:
            # 降级：使用草稿
            print("  润色失败，使用草稿版本")
            return draft_title, draft_content

        print(f"  最终标题: {final_title}")
        print(f"  最终字数: {len(final_content)}")

        return final_title, final_content

    async def _call_llm(self, llm, system_prompt: str, user_prompt: str, label: str):
        """调用 LLM 并解析 JSON 返回结果"""
        from browser_use.llm import SystemMessage, UserMessage

        messages = [
            SystemMessage(content=system_prompt),
            UserMessage(content=user_prompt),
        ]

        response = await llm.ainvoke(messages)
        # ainvoke returns ChatInvokeCompletion with .completion field
        raw = response.completion
        raw_text = str(raw) if raw is not None else ""

        parsed = self._parse_json(raw_text)
        if not parsed:
            print(f"  ⚠ {label} JSON 解析失败，原始回复前200字: {raw_text[:200]}")
            return None, None

        title = parsed.get("title", "").strip()
        content = parsed.get("content", "").strip()
        return title, content

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """容错 JSON 解析：直接解析 → markdown code block → 暴力提取 → 正则字段提取"""
        # 1. 直接解析
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

        # 2. 提取 markdown code block
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, TypeError):
                pass

        # 3. 暴力提取第一个 { ... } 块
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except (json.JSONDecodeError, TypeError):
                            pass
                        break

        # 4. 正则提取 title 和 content 字段（终极兜底）
        title_m = re.search(r'"title"\s*:\s*"([^"]*)"', text)
        content_m = re.search(r'"content"\s*:\s*"((?:(?!"content":|"title":).)*)"', text, re.DOTALL)
        if title_m:
            result = {"title": title_m.group(1)}
            if content_m:
                result["content"] = content_m.group(1)
            else:
                # 如果 content 匹配失败，尝试取 title 之后的所有内容
                content_start = title_m.end()
                # 找到 content: " 的位置
                c_marker = re.search(r'"content"\s*:\s*"', text[content_start:])
                if c_marker:
                    raw_content = text[content_start + c_marker.start() + len('"content": "'):].rstrip('"}\n')
                    result["content"] = raw_content
            return result

        return None


# ============================================================
# CLI
# ============================================================


def print_topics(topics: list[dict]):
    """打印热点话题列表"""
    print(f"\n当前头条热榜（共 {len(topics)} 条）：\n")
    for i, t in enumerate(topics):
        hot = format_hot_value(t["hot_value"])
        print(f"  [{i:2d}] {t['title']}  (热度: {hot})")
    print()


def save_article(title: str, content: str, output_path: Optional[str] = None):
    """保存文章到 JSON 文件"""
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"article_{ts}.json"

    data = {
        "title": title,
        "content": content,
        "generated_at": datetime.now().isoformat(),
        "source": "hot_topic_generator",
    }

    out = Path(output_path)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n文章已保存: {out}")
    return str(out)


async def main():
    parser = argparse.ArgumentParser(
        description="热点文章自动生成器 — 头条热榜 + LLM 两轮生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  1. 列出热点:   python hot_topic_generator.py --list-topics
  2. 生成文章:   python hot_topic_generator.py --generate
  3. 指定话题:   python hot_topic_generator.py --generate --topic-index 3
  4. 自动发布:   python hot_topic_generator.py --auto-publish
        """,
    )
    parser.add_argument("--list-topics", action="store_true", help="列出当前头条热点话题")
    parser.add_argument("--generate", action="store_true", help="基于热点话题生成文章")
    parser.add_argument("--topic-index", type=int, default=0, help="选择第几个话题（0-based，默认0）")
    parser.add_argument("--topic-count", type=int, default=20, help="获取热点话题数量（默认20）")
    parser.add_argument("--output", type=str, help="文章保存路径（JSON 格式）")
    parser.add_argument("--auto-publish", action="store_true", help="生成后自动发布到头条号")

    args = parser.parse_args()

    fetcher = HotTopicFetcher()

    # --- 列出热点 ---
    if args.list_topics:
        print("正在获取头条热榜...")
        try:
            topics = await fetcher.fetch(count=args.topic_count)
        except Exception as e:
            print(f"获取热点失败: {e}")
            sys.exit(1)
        print_topics(topics)
        return

    # --- 生成文章 ---
    if args.generate or args.auto_publish:
        print("正在获取头条热榜...（自动过滤敏感话题）")

        # 获取 LLM 用于语义过滤
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from publisher import get_browser_llm
            llm = get_browser_llm()
        except Exception:
            print("  ⚠ 无法加载 LLM，仅使用关键词过滤")
            llm = None

        try:
            topics = await fetcher.fetch(count=max(args.topic_count, args.topic_index + 1), llm=llm)
        except Exception as e:
            print(f"获取热点失败: {e}")
            sys.exit(1)

        if not topics:
            print("未获取到任何热点话题")
            sys.exit(1)

        # 找到第一个非重复的热点话题
        existing_articles = load_existing_articles()
        if existing_articles:
            print(f"  本地已有 {len(existing_articles)} 篇文章，检查话题是否重复...")

        topic = None
        for t in topics:
            dup, matched_title = is_topic_duplicate(t["title"])
            if dup:
                print(f"  跳过重复话题: {t['title']} (与已有文章「{matched_title}」重复)")
                continue
            topic = t
            break

        if not topic:
            print("所有热点话题均已生成过文章，无法继续")
            sys.exit(1)

        print(f"\n选定话题: {topic['title']} (热度: {format_hot_value(topic['hot_value'])})\n")

        generator = ArticleGenerator()
        try:
            title, content = await generator.generate(topic)
        except Exception as e:
            print(f"生成文章失败: {e}")
            sys.exit(1)

        if not content:
            print("生成失败：未获取到有效内容")
            sys.exit(1)

        # 保存到 test_perper/{文章名}/article.txt
        article_dir = save_article_to_test_perper(title, content)

        # 生成封面图片
        print(f"\n正在生成封面图片...")
        cover_path = await generate_cover_image(topic["title"], title, output_dir=Path(article_dir))

        # 同时保存 JSON 副本（用于追溯）
        if args.output:
            save_article(title, content, args.output)

        # 自动发布
        if args.auto_publish:
            print(f"\n正在自动发布到头条号...")
            sys.path.insert(0, str(Path(__file__).parent))
            from publisher import publish_article, write_result

            result = await publish_article(title=title, content=content)
            write_result(result)
        else:
            print(f"\n标题: {title}")
            print(f"正文前200字: {content[:200]}...")
            print(f"\n如需发布，运行:")
            print(f'  python publisher.py --publish --title "{title}" --content-file {args.output or "article_*.json"}')
        return

    parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
