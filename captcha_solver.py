"""
头条号滑块验证码求解模块
纯 LLM 方案：MIMO v2.5 直接识别滑块百分比

- MIMO v2.5：看图直接说出滑块应拖到轨道的多少百分比（33/50/67）
- 不需要 OpenCV
"""

import asyncio
import base64
import io
import math
import os
import random
import re
from typing import Tuple

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()


def get_mimo_client() -> OpenAI:
    api_key = os.getenv("MIMO_API_KEY")
    if not api_key:
        raise RuntimeError("Please set MIMO_API_KEY in .env")
    return OpenAI(
        api_key=api_key,
        base_url="https://token-plan-cn.xiaomimimo.com/v1",
    )


def _compress_image(screenshot_bytes: bytes, max_size: Tuple[int, int] = (400, 350)) -> bytes:
    img = Image.open(io.BytesIO(screenshot_bytes))
    img = img.resize(max_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def identify_slider_percent(screenshot_bytes: bytes) -> float:
    """
    用 MIMO v2.5 识别滑块应拖到轨道的多少百分比位置。

    Returns:
        滑块百分比（10~100）
    """
    client = get_mimo_client()
    compressed = _compress_image(screenshot_bytes)
    b64 = base64.b64encode(compressed).decode()
    data_uri = f"data:image/png;base64,{b64}"

    try:
        resp = client.chat.completions.create(
            model="mimo-v2.5",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "这是一个滑块拼图验证码。"
                            "底部有一个滑块轨道，左边的图形需要通过拖动滑块来与右边目标位置的图形重合。"
                            "滑块轨道的最左端是0%，最右端是100%。"
                            "请观察左边的图形和右边哪个目标位置匹配，"
                            "然后告诉我滑块应该拖到轨道的多少百分比。"
                            "只回答一个数字（如20、35、50、67、100），不要其他内容。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
            max_tokens=10,
            timeout=20,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = (resp.choices[0].message.content or "").strip()

        for token in re.findall(r'\d+', content):
            v = int(token)
            if 10 <= v <= 100:
                return float(v)

        raise ValueError(f"MIMO returned unparseable: '{content}'")

    except Exception as e:
        raise RuntimeError(f"MIMO recognition failed: {e}")


def generate_human_trajectory(
    start_x: int,
    start_y: int,
    distance: int,
    num_points: int = 40,
) -> list:
    jitter_amplitude = random.uniform(2, 8)
    jitter_frequency = random.uniform(2, 4)
    overshoot = random.uniform(2, 5)
    overshoot_start = random.uniform(0.85, 0.92)

    points = []
    for i in range(num_points + 1):
        t = i / num_points
        if t < overshoot_start:
            local_t = t / overshoot_start
            local_eased = (1 - math.cos(local_t * math.pi)) / 2
            x = start_x + int(distance * local_eased)
        else:
            correction_t = (t - overshoot_start) / (1 - overshoot_start)
            overshoot_amount = overshoot * math.sin(correction_t * math.pi)
            x = start_x + distance + int(overshoot_amount)

        y_offset = int(jitter_amplitude * math.sin(t * jitter_frequency * math.pi))
        y = start_y + y_offset
        points.append((x, y))

    points[-1] = (start_x + distance, start_y)
    return points


async def execute_drag(page, trajectory: list) -> None:
    if not trajectory:
        return

    start_x, start_y = trajectory[0]
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.1, 0.2))

    total = len(trajectory)
    for i, (x, y) in enumerate(trajectory):
        await page.mouse.move(x, y)
        progress = i / max(total - 1, 1)
        delay = 15 + 35 * math.sin(progress * math.pi)
        await asyncio.sleep(delay / 1000.0)

    await asyncio.sleep(random.uniform(0.1, 0.3))
    await page.mouse.up()
