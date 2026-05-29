#!/usr/bin/env python3
"""
并发发布测试 - 两个账号同时发文
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def load_article(folder: Path) -> tuple[str, str]:
    """从文件夹读取文章，返回 (标题, 正文)"""
    art = folder / "article.txt"
    content = art.read_text(encoding="utf-8").strip()
    lines = content.split('\n')
    title = lines[0].lstrip('#').strip()
    body_start = 1
    if len(lines) > 1 and lines[1].strip() == '':
        body_start = 2
    body = '\n'.join(lines[body_start:]).strip()
    if not body:
        body = content
    return title, body


def get_images(folder: Path) -> list[str]:
    """获取文件夹下所有图片的绝对路径"""
    exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    return [str(f) for f in sorted(folder.iterdir()) if f.is_file() and f.suffix.lower() in exts]


async def main():
    TEST_DIR = Path(__file__).parent / "test_perper"

    # 使用 topics_used.json 判断文章是否已发布
    import json, re
    topics_used_file = TEST_DIR / "topics_used.json"
    used_titles = set()
    if topics_used_file.exists():
        try:
            used_data = json.loads(topics_used_file.read_text(encoding="utf-8"))
            for item in used_data:
                used_titles.add(item.get("article_title", "").strip())
        except Exception:
            pass

    # 扫描所有 perper 文件夹（排除 topics_used.json 等非文件夹）
    all_folders = sorted([
        d for d in TEST_DIR.iterdir()
        if d.is_dir() and d.name.startswith("perper")
    ])

    # 按基础编号分组，每个组取最新的版本（最大 _N 或无 _N 的）
    base_map = {}
    for d in all_folders:
        m = re.match(r'^(perper\d+)(?:_(\d+))?$', d.name)
        if not m:
            continue
        base = m.group(1)
        ver = int(m.group(2)) if m.group(2) else 999  # 无 _N 的优先级最高
        if base not in base_map or ver > base_map[base][1]:
            base_map[base] = (d, ver)

    # 选取未发布的文章：标题不在 topics_used.json 中
    unpub = []
    for base, (folder, ver) in sorted(base_map.items()):
        art = folder / "article.txt"
        if not art.exists():
            continue
        content = art.read_text(encoding="utf-8").strip()
        title = content.split('\n')[0].lstrip('#').strip()
        if title not in used_titles:
            unpub.append(folder)

    if len(unpub) < 2:
        print(f"未发布文章不足 2 篇（当前 {len(unpub)} 篇 {[f.name for f in unpub]})")
        # 列出所有文章供参考
        print("\n所有文章:")
        for base, (folder, ver) in sorted(base_map.items()):
            art = folder / "article.txt"
            if art.exists():
                t = art.read_text(encoding="utf-8").strip().split('\n')[0].lstrip('#').strip()
                status = "已发布" if t in used_titles else "未发布"
                print(f"  {folder.name} [{status}] {t[:50]}")
        return

    f1 = unpub[0]
    f2 = unpub[1]

    t1, c1 = load_article(f1)
    t2, c2 = load_article(f2)
    imgs1 = get_images(f1)
    imgs2 = get_images(f2)

    print(f"=== 并发发布测试 ===")
    print(f"  user1 → {f1.name}: {t1} ({len(c1)}字) [{len(imgs1)}张图片]")
    print(f"  user2 → {f2.name}: {t2} ({len(c2)}字) [{len(imgs2)}张图片]")
    print()

    from concurrent_publisher import publish_batch

    start = asyncio.get_event_loop().time()

    results = await publish_batch([
        {"user_id": "user1", "title": t1, "content": c1, "image_paths": imgs1},
        {"user_id": "user2", "title": t2, "content": c2, "image_paths": imgs2},
    ])

    elapsed = asyncio.get_event_loop().time() - start

    print(f"\n=== 发布结果（耗时 {elapsed:.1f}s） ===")
    for r in results:
        uid = r.get("user_id", "?")
        ok = "✅ 成功" if r.get("success") else "❌ 失败"
        url = r.get("article_url", "")
        reason = r.get("failure_reason", "")
        print(f"  {uid}: {ok}")
        if url:
            print(f"    URL: {url}")
        if reason:
            print(f"    原因: {reason[:100]}")

    # 发布成功后标记
    for r in results:
        if r.get("success"):
            uid = r["user_id"]
            folder = f1 if uid == "user1" else f2
            art = folder / "article.txt"
            title = art.read_text(encoding="utf-8").strip().split('\n')[0].lstrip('#').strip()

            # 记录到 topics_used.json
            used_data = []
            if topics_used_file.exists():
                try:
                    used_data = json.loads(topics_used_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            from datetime import datetime
            used_data.append({
                "topic_title": title,
                "article_title": title,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            topics_used_file.write_text(json.dumps(used_data, ensure_ascii=False, indent=2), encoding="utf-8")

            # 重命名文件夹
            base = folder.name
            count = len([
                d for d in TEST_DIR.iterdir()
                if d.is_dir() and d.name.startswith(base + "_")
            ])
            new_name = f"{base}_{count + 1}"
            folder.rename(TEST_DIR / new_name)
            print(f"  已标记: {base} → {new_name}")


if __name__ == "__main__":
    asyncio.run(main())
