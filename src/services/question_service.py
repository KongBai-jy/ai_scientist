"""
Science 125 问题提取服务
========================

供前端「开始研究」弹窗动态展示（替代硬编码的 SAMPLE_QUESTIONS）。

问题数据源为硬编码文件 data/sci2025_problems.json，每条 {en, cn, category}，
覆盖 Science 125 问题的全部真实条目，category 为中文领域。数据源缺失/损坏时返回 []。
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 硬编码问题数据源（唯一来源）：data/sci2025_problems.json
_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "sci2025_problems.json"

_cache: Optional[List[Dict[str, Any]]] = None


def _load_data_file() -> Optional[List[Dict[str, Any]]]:
    """从数据源加载完整问题列表。

    Returns:
        [{question: 中文, question_en: 英文, category: 中文领域}, ...]；文件缺失/损坏返回 None。
    """
    try:
        if not _DATA_FILE.exists():
            return None
        data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        out = []
        for d in data:
            en = (d.get("en") or "").strip()
            cn = (d.get("cn") or "").strip()
            cat = (d.get("category") or "未分类").strip()
            if en and cn:
                out.append({
                    "question": cn,
                    "question_en": en,
                    "category": cat,
                })
        return out or None
    except Exception as e:
        logger.warning(f"读取问题数据源失败: {e}")
        return None


def get_science_questions(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    读取 Science 125 问题清单（带进程内缓存）。

    Returns:
        [{"question": "中文翻译", "question_en": "英文原文", "category": "数学科学"}, ...]
        数据源不可用时返回 []
    """
    global _cache
    if _cache is not None and not force_refresh:
        return _cache

    from_file = _load_data_file()
    _cache = from_file if from_file else []
    if from_file:
        logger.info(f"读取 Science 问题 {len(_cache)} 条")
    return _cache