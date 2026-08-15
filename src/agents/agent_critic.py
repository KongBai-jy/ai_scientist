"""
Agent 3：评审官（Critic）
职责：多维评分 + 反事实攻击 + 缺陷诊断
基于 LangChain 1.x + 千问模型
"""

import json
import logging
import os
import time
from typing import List, Dict, Any, Optional, Literal
from pathlib import Path
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from models.schemas import (
    CriticInput, CriticOutput,
    DimensionScores, Hypothesis
)

# 加载项目根目录的 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)


# ============================================================
# 1. LLM 配置
# ============================================================

_DEFAULT_MODEL = ""
_DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

llm = ChatOpenAI(
    model=os.getenv("QWEN_MODEL", _DEFAULT_MODEL),
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_API_BASE", _DEFAULT_API_BASE),
    temperature=0.5,  # 评审需要更稳定
    max_tokens=4096,
    timeout=180.0,
)


# ============================================================
# 2. Prompt 模板
# ============================================================

SYSTEM_PROMPT = """你是一位顶尖期刊（如 Nature/Science）的严苛审稿人，擅长对科学假设进行批判性评审。

## 评审维度（0-10 分）
1. **证据支撑度**：假设是否扎根于可信文献或跨域类比
   - 9-10分：强证据链，多源印证
   - 7-8分：有直接证据，但不够充分
   - 5-6分：间接证据或类比支撑
   - 0-4分：缺乏证据支撑
2. **可证伪性**：推翻假设的条件是否明确、可操作
   - 9-10分：有具体阈值和观测条件
   - 7-8分：有明确条件但不够具体
   - 5-6分：可证伪但模糊
   - 0-4分：几乎不可证伪
3. **理论一致性**：是否与现有科学框架自洽
   - 9-10分：完全自洽，符合主流理论
   - 7-8分：基本自洽，有轻微冲突
   - 5-6分：部分冲突
   - 0-4分：严重冲突
4. **新颖度**：是否提出新视角或新连接
   - 9-10分：原创性极高
   - 7-8分：有新意但不够突破
   - 5-6分：常规假设
   - 0-4分：已有大量类似研究
5. **跨学科适配度**：借用的方法论是否合理匹配
   - 9-10分：完美适配
   - 7-8分：合理适配
   - 5-6分：基本可用
   - 0-4分：生搬硬套

## 核心要求
1. 对每条假设给出 5 维评分
2. 诊断 Top-1 致命缺陷
3. 构造反事实"必败条件"（该假设在什么极端条件下必然失效）
4. 列出缺失证据清单（供下一轮迭代修复）

## 输出格式（纯 JSON）
{
  "scores": {
    "evidence": 7.5,
    "falsifiability": 6.0,
    "consistency": 8.0,
    "novelty": 7.0,
    "cross_domain": 6.5
  },
  "top_flaw": "最致命的缺陷描述",
  "counterfactual": "在 XX 极端条件下，该假设必然被推翻",
  "missing_evidences": ["缺失证据1", "缺失证据2"],
  "detailed_review": "详细评审意见（Markdown 格式）"
}
"""


# ============================================================
# 3. 核心函数
# ============================================================

def critique(
    hypotheses: List[Dict[str, Any]],
    max_retries: int = 3
) -> CriticOutput:
    """
    执行评审

    Args:
        hypotheses: 假设列表
        max_retries: 最大重试次数

    Returns:
        CriticOutput: 包含评分、缺陷、反事实条件

    Raises:
        RuntimeError: 超过最大重试次数仍失败
    """
    structured_llm = llm.with_structured_output(CriticOutput)

    hypotheses_str = json.dumps(hypotheses, ensure_ascii=False, indent=2)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"""
## 待评审假设列表
{hypotheses_str}

请对以上假设进行严格评审，按 JSON 格式输出评分、缺陷、反事实条件和缺失证据。

注意：
1. 反事实条件（counterfactual）必须具体、极端，例如："若未来探测器精度无法达到 10^-12 量级，则该假设无法验证"
2. 缺失证据（missing_evidences）应具体到可检索的关键词
""")
    ]

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Agent 3 评审尝试 {attempt}/{max_retries}...")
            result = structured_llm.invoke(messages)

            # 校验评分范围
            scores = result.scores
            for field, value in scores.model_dump().items():
                if not (0 <= value <= 10):
                    raise ValueError(f"评分 {field}={value} 超出 0-10 范围")

            # 校验反事实条件长度
            if len(result.counterfactual) < 15:
                raise ValueError(f"反事实条件过短 ({len(result.counterfactual)} < 15)")

            logger.info(f"✅ 评审完成")
            return result

        except Exception as e:
            logger.warning(f"第 {attempt} 次失败: {e}")
            last_error = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"评审失败: {last_error}")


# ============================================================
# 4. 计算综合评分（辅助函数）
# ============================================================

WEIGHTS = {
    "evidence": 0.25,
    "falsifiability": 0.25,
    "consistency": 0.20,
    "novelty": 0.15,
    "cross_domain": 0.15,
}


def calculate_overall_score(scores: DimensionScores, penalty: float = 0.0) -> float:
    """计算综合得分"""
    raw = (
        scores.evidence * WEIGHTS["evidence"] +
        scores.falsifiability * WEIGHTS["falsifiability"] +
        scores.consistency * WEIGHTS["consistency"] +
        scores.novelty * WEIGHTS["novelty"] +
        scores.cross_domain * WEIGHTS["cross_domain"]
    )
    return round(max(0, raw - penalty), 2)


# ============================================================
# 5. LangGraph 节点
# ============================================================

class CriticState(BaseModel):
    hypotheses: List[Dict[str, Any]] = Field(default_factory=list)
    critic_output: Optional[CriticOutput] = None
    retry_count: int = 0
    errors: List[str] = Field(default_factory=list)


def critic_node(state: dict) -> dict:
    """LangGraph 节点函数"""
    logger.info("进入 Critic Node")

    try:
        result = critique(state["hypotheses"])
        return {
            "critic_output": result.model_dump(),
            "retry_count": 0,
            "errors": []
        }
    except Exception as e:
        logger.error(f"Critic Node 失败: {e}")
        return {
            "critic_output": None,
            "retry_count": state.get("retry_count", 0) + 1,
            "errors": [str(e)]
        }


def build_critic_graph():
    """构建 Critic 子图"""
    workflow = StateGraph(CriticState)

    workflow.add_node("critic", critic_node)
    workflow.set_entry_point("critic")

    def should_continue(state: dict) -> str:
        if state.get("errors") and state.get("retry_count", 0) < 3:
            return "critic"
        return "__end__"

    workflow.add_conditional_edges(
        "critic",
        should_continue,
        {
            "critic": "critic",
            "__end__": END
        }
    )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)