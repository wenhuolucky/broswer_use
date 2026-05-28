#!/usr/bin/env python3
"""
豆包 AI 检测测试脚本
每分钟自动向豆包发送问题，收集回复，用于测试自动化内容能否逃过 AI 检测

用法:
  python doubao/doubao_tester.py --setup
  python doubao/doubao_tester.py --loop
  python doubao/doubao_tester.py --test-single --question "你好"
  python doubao/doubao_tester.py --loop --interval 60 --max-iterations 10
"""

import argparse
import asyncio
import json
import logging
import random
import sys
import signal
from datetime import datetime
from pathlib import Path

# Windows 控制台默认 GBK 编码，设为 UTF-8 支持 emoji 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
RESULT_DIR = BASE_DIR / "result"
LOG_DIR = BASE_DIR / "log"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 添加父目录到路径，以便导入 doubao 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from doubao.questions import get_random_question, rotate_question, get_question_count
from doubao.storage import QAStorage, create_qa_record
from doubao.browser_session import setup_login, launch_browser_once, create_browser_session, send_question
from doubao.human_behavior import random_delay


class DoubaoTester:
    """豆包测试器主类"""

    def __init__(self, interval: int = 60, skip_nav: bool = False):
        self.interval = interval
        self.skip_nav = skip_nav
        self.storage = QAStorage()
        self.current_question_index = 0
        self.running = True
        self.session = None

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """处理 Ctrl+C 和终止信号，实现优雅退出"""
        print("\n\n收到退出信号，正在关闭...")
        self.running = False

    def _write_result(self, record: dict):
        """将单轮结果写入 result/ 目录"""
        ts = record.get("timestamp", datetime.now().isoformat()).replace(":", "").replace("-", "").replace("T", "_").split(".")[0]
        filename = f"result_{ts}_{record.get('question', 'unknown')[:20]}.json"
        # 清理文件名非法字符
        filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
        filepath = RESULT_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        print(f"结果已保存: {filepath}")

    async def test_single(self, question: str = None) -> None:
        """单次测试：发送一个问题并收集回复"""
        print("=" * 50)
        print("豆包 AI 检测测试 - 单次执行")
        print("=" * 50)

        # 获取问题
        if question:
            q_data = {"type": "manual", "question": question, "purpose": "手动指定"}
            q_index = -1
        else:
            q_data = get_random_question()
            q_index = self.current_question_index

        question_text = q_data["question"]
        print(f"\n问题 ({q_data['type']}): {question_text}")
        print(f"目的: {q_data['purpose']}")

        # 启动浏览器进程
        print("\n正在启动浏览器进程...")
        try:
            playwright, ctx = await launch_browser_once()
        except Exception as e:
            print(f"启动浏览器失败: {e}")
            return

        # 通过 CDP 连接
        print("正在创建浏览器会话...")
        try:
            self.session = await create_browser_session()
        except Exception as e:
            print(f"创建浏览器会话失败: {e}")
            return

        try:
            # 发送问题
            print("正在发送问题...")
            response, elapsed_ms = await send_question(
                self.session, question_text, skip_nav=self.skip_nav
            )

            print(f"响应时间: {elapsed_ms} ms")
            print(f"\n回复内容:\n{response}")

            # 存储记录
            record = create_qa_record(
                question=question_text,
                response=response,
                question_index=q_index,
                response_time_ms=elapsed_ms,
                error=None if response else "No response"
            )
            self.storage.append(record)
            self._write_result(record)

            stats = self.storage.get_stats()
            print(f"\n已保存记录. 今日统计: {stats['success']} 成功, {stats['errors']} 错误")
        finally:
            # 关闭会话；不再调用 playwright.stop()，因为方案 B 下浏览器由用户手动管理
            if self.session:
                await self.session.close()
            print("会话已断开（浏览器仍在运行，请手动关闭）")

    async def run_loop(self, max_iterations: int = None) -> None:
        """循环测试：在同一个浏览器进程和对话中连续提问"""
        print("=" * 50)
        print("豆包 AI 检测测试 - 循环模式")
        print("=" * 50)
        print(f"间隔: {self.interval} 秒")
        if self.skip_nav:
            print("模式：已在页面（跳过导航，方案 B）")
        else:
            print("模式：完整流程（含导航）")
        print(f"同一对话：所有问题在同一个对话记录中发送")

        if max_iterations:
            print(f"最大迭代次数: {max_iterations}")
        else:
            print("无限循环，按 Ctrl+C 退出")

        # 启动浏览器（常驻进程）
        print("\n正在启动浏览器进程...")
        try:
            playwright, ctx = await launch_browser_once()
        except Exception as e:
            print(f"启动浏览器失败: {e}")
            return

        iteration = 0
        all_records = []

        while self.running:
            iteration += 1
            iter_start = datetime.now()
            print(f"\n{'=' * 50}")
            print(f"迭代 #{iteration}/{max_iterations or '∞'} - {iter_start.strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 50)

            # 每轮独立日志
            log_file = LOG_DIR / f"iter_{iteration:03d}_{iter_start.strftime('%Y%m%d_%H%M%S')}.log"
            logger = logging.getLogger(f"iter_{iteration}")
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)

            # 每次迭代重新连接 CDP（agent.run 后会断开连接，但浏览器进程仍在）
            try:
                self.session = await create_browser_session()
            except Exception as e:
                msg = f"连接浏览器失败: {e}"
                print(msg)
                logger.error(msg)
                q_data, self.current_question_index = rotate_question(self.current_question_index)
                record = create_qa_record(
                    question=q_data["question"], response="",
                    question_index=self.current_question_index,
                    response_time_ms=0, error=msg
                )
                self.storage.append(record)
                self._write_result(record)
                all_records.append(record)
                logger.handlers.clear()
                if self.running:
                    for _ in range(self.interval):
                        if not self.running:
                            break
                        await asyncio.sleep(1)
                continue

            # 获取问题
            q_data, self.current_question_index = rotate_question(self.current_question_index)
            question_text = q_data["question"]

            print(f"问题 ({q_data['type']}): {question_text}")
            print(f"目的: {q_data['purpose']}")
            logger.info(f"问题: {question_text} | 类型: {q_data['type']}")

            # 发送问题（每轮都新建对话，保持页面干净，避免 DOM 积累）
            print("正在发送问题...")
            response, elapsed_ms = await send_question(
                self.session, question_text, max_steps=50, skip_nav=self.skip_nav
            )

            iter_elapsed = (datetime.now() - iter_start).total_seconds()
            logger.info(f"响应时间: {elapsed_ms}ms | 总耗时: {iter_elapsed:.1f}s")

            print(f"响应时间: {elapsed_ms} ms")

            if response:
                display_response = response[:200] + "..." if len(response) > 200 else response
                print(f"\n回复摘要:\n{display_response}")
                error = None
            else:
                print("\n未收到有效回复")
                error = "No response received"

            # 存储记录
            record = create_qa_record(
                question=question_text,
                response=response,
                question_index=self.current_question_index,
                response_time_ms=elapsed_ms,
                error=error
            )
            record["iteration"] = iteration
            record["total_seconds"] = round(iter_elapsed, 1)
            self.storage.append(record)
            self._write_result(record)
            all_records.append(record)

            stats = self.storage.get_stats()
            print(f"\n今日统计: {stats['success']} 成功, {stats['errors']} 错误")

            logger.info(f"结果: {'成功' if not error else error}")
            logger.handlers.clear()

            # 检查是否达到最大迭代次数
            if max_iterations and iteration >= max_iterations:
                print(f"\n已达到最大迭代次数 {max_iterations}，退出循环")
                break

            # 随机人类延迟（2-10秒），模拟人类节奏
            delay = await random_delay(2.0, 10.0)
            print(f"[人类行为] 随机延迟 {delay} 秒")

            # 等待下一个间隔
            if self.running:
                print(f"\n等待 {self.interval} 秒后进行下一次...")
                for _ in range(self.interval):
                    if not self.running:
                        break
                    await asyncio.sleep(1)

        # 退出前断开会话；方案 B 下不关闭浏览器进程（由用户手动管理）
        if self.session:
            print("\n正在断开会话...")
            await self.session.close()
        # 兼容方案 A 残留：如果 launch_browser_once 返回了真实 playwright（None 时跳过）
        if 'playwright' in locals() and playwright is not None:
            await playwright.stop()
        print("会话已断开（浏览器仍在运行，请手动关闭）")

        # 写汇总报告
        summary_file = RESULT_DIR / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_iterations": len(all_records),
            "success": sum(1 for r in all_records if not r.get("error")),
            "errors": sum(1 for r in all_records if r.get("error")),
            "avg_response_time_ms": sum(r.get("response_time_ms", 0) for r in all_records) / max(len(all_records), 1),
            "records": all_records,
        }
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n汇总报告: {summary_file}")
        print("\n测试已结束")


async def main():
    parser = argparse.ArgumentParser(
        description="豆包 AI 检测测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  1. 首次登录: python doubao/doubao_tester.py --setup
  2. 单次测试: python doubao/doubao_tester.py --test-single --question "你好"
  3. 循环测试: python doubao/doubao_tester.py --loop
  4. 自定义:   python doubao/doubao_tester.py --loop --interval 60 --max-iterations 10
        """
    )

    parser.add_argument("--setup", action="store_true",
                        help="首次: 打开浏览器手动登录豆包")
    parser.add_argument("--test-single", action="store_true",
                        help="单次执行测试")
    parser.add_argument("--loop", action="store_true",
                        help="循环执行测试（默认间隔60秒）")
    parser.add_argument("--question", type=str,
                        help="指定测试问题（用于 --test-single）")
    parser.add_argument("--interval", type=int, default=60,
                        help="循环测试的间隔秒数（默认60）")
    parser.add_argument("--max-iterations", type=int,
                        help="最大迭代次数（默认无限）")
    parser.add_argument("--skip-nav", action="store_true",
                        help="跳过导航步骤（方案 B 时使用，浏览器已在目标页面）")

    args = parser.parse_args()

    if args.setup:
        print("=" * 50)
        print("豆包登录设置")
        print("=" * 50)
        await setup_login()

    elif args.test_single:
        tester = DoubaoTester(interval=args.interval, skip_nav=args.skip_nav)
        await tester.test_single(question=args.question)

    elif args.loop:
        tester = DoubaoTester(interval=args.interval, skip_nav=args.skip_nav)
        await tester.run_loop(max_iterations=args.max_iterations)

    else:
        parser.print_help()
        print("\n请选择一种模式运行:")
        print("  --setup       登录设置")
        print("  --test-single 单次测试")
        print("  --loop        循环测试")


if __name__ == "__main__":
    asyncio.run(main())
