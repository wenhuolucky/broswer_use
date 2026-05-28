"""
随机人类行为模拟
在浏览器自动化中插入随机延迟和行为，降低被风控检测的风险
"""

import asyncio
import random


async def random_delay(min_sec: float = 2.0, max_sec: float = 10.0) -> float:
    """
    随机等待 min_sec ~ max_sec 秒，模拟人类节奏。
    默认范围 2-10 秒，比机器更"人"——人类操作之间不会精确等 5 秒。
    返回实际等待秒数。
    """
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)
    return round(delay, 1)


async def random_behavior() -> str:
    """
    随机执行一种人类行为（滚动、停留、鼠标轨迹）。
    返回行为描述字符串，失败返回 None。
    该函数不操作浏览器，仅返回描述——实际行为由 Agent 任务指令执行。
    """
    behaviors = [
        ("scroll", "在页面上随机滚动几屏（上下均可），模拟人类浏览行为"),
        ("pause", "在页面上停留 3-10 秒，模拟人类阅读或思考"),
        ("hover", "鼠标在页面上随机移动几次，模拟人类鼠标轨迹"),
        ("history", "点击左侧历史记录栏，翻看 1-2 个旧对话，然后返回当前对话"),
    ]
    name, desc = random.choice(behaviors)
    return f"[人类行为] {desc}"
