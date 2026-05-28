"""
JSONL 数据存储模块
负责问答记录的持久化存储
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class QAStorage:
    """问答记录存储管理器"""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent / "data"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_today_file(self) -> Path:
        """获取今天的 JSONL 文件路径"""
        today = datetime.now().strftime("%Y%m%d")
        return self.data_dir / f"doubao_qa_{today}.jsonl"

    def append(self, record: dict) -> None:
        """
        追加一条问答记录到今天的 JSONL 文件

        Args:
            record: 包含以下字段的字典:
                - timestamp: ISO 格式时间戳
                - question_index: 问题索引
                - question: 问题内容
                - response: 回复内容
                - response_time_ms: 响应时间（毫秒）
                - error: 错误信息（若有）
        """
        file_path = self._get_today_file()

        # 确保 timestamp 存在
        if "timestamp" not in record:
            record["timestamp"] = datetime.now().isoformat()

        # 追加到文件（每行一个 JSON）
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_today_records(self) -> list:
        """加载今天的所有记录"""
        file_path = self._get_today_file()
        if not file_path.exists():
            return []

        records = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def get_stats(self) -> dict:
        """获取今天的统计数据"""
        records = self.load_today_records()
        return {
            "total": len(records),
            "success": len([r for r in records if r.get("error") is None]),
            "errors": len([r for r in records if r.get("error")]),
            "file": str(self._get_today_file())
        }


def create_qa_record(
    question: str,
    response: str,
    question_index: int,
    response_time_ms: int = 0,
    error: Optional[str] = None
) -> dict:
    """
    创建一条问答记录

    Args:
        question: 问题内容
        response: 回复内容
        question_index: 问题索引
        response_time_ms: 响应时间（毫秒）
        error: 错误信息

    Returns:
        标准格式的问答记录字典
    """
    return {
        "timestamp": datetime.now().isoformat(),
        "question_index": question_index,
        "question": question,
        "response": response,
        "response_length": len(response) if response else 0,
        "response_time_ms": response_time_ms,
        "error": error
    }
