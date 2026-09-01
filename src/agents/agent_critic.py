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
from utils.llm_structured_fallback import parse_llm_json_to_model

# 加载项目根目录的 .env
import sys
if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
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
    temperature=0.3,  # 降低温度：评审需要更稳定、可复现的评分
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
1. 对假设集整体给出一个综合 5 维评分（禁止逐条输出；如需逐条分析，写入 detailed_review 字段）
2. 诊断 Top-1 致命缺陷
3. 构造反事实"必败条件"（该假设在什么极端条件下必然失效）
4. 列出缺失证据清单（供下一轮迭代修复）

## 输出格式（纯 JSON，字段名严格一致，嵌套对象必须为对象不可写字符串）
- scores (对象) —— 必须是嵌套对象，五个键的取值 0-10 之间浮点数：
    * evidence (float) —— 证据支撑度
    * falsifiability (float) —— 可证伪性
    * consistency (float) —— 理论一致性
    * novelty (float) —— 新颖度
    * cross_domain (float) —— 跨学科适配度
- top_flaw (字符串，≥10 字符)
- counterfactual (字符串，≥15 字符)
- counterfactual_severity (float，0-10) —— 反事实条件严苛度评分：
    * 9-10分：条件极端苛刻，在现实中几乎不可能满足
    * 7-8分：条件非常苛刻，需要极端技术突破
    * 5-6分：条件有一定难度，但并非完全不可能
    * 3-4分：条件相对温和，现有条件基本可满足
    * 0-2分：条件几乎不构成实质挑战
- counterfactual_vulnerability (float，0-10) —— 假设在该反事实条件下的脆弱度评分：
    * 9-10分：假设在该条件下立即被推翻，无任何回旋余地
    * 7-8分：假设核心逻辑被严重动摇，仅部分残余
    * 5-6分：假设需要重大修改才能存活
    * 3-4分：假设可通过局部调整继续成立
    * 0-2分：假设几乎不受影响，轻松抵御
- missing_evidences (字符串数组)
- detailed_review (字符串，≥30 字符)

【正确示例】
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
  "counterfactual_severity": 7.5,
  "counterfactual_vulnerability": 6.0,
  "missing_evidences": ["缺失证据1", "缺失证据2"],
  "detailed_review": "详细评审意见（Markdown 格式）"
}

【严禁】
- "scores": "evidence: 7.5, falsifiability: 6.0..."   —— 字符串，错误
- 输出 JSON 数组 / 逐条假设的评审列表 —— 数组，错误；必须且只能输出一个 JSON 对象

## 语言要求（强制）
- 所有文本字段（top_flaw、counterfactual、missing_evidences、detailed_review 等）**必须使用中文**
- 评分维度名（evidence、falsifiability、consistency、novelty、cross_domain）保持英文键名不变
- 允许保留的英文仅限：专有名词、arXiv 编号、DOI、标准学科术语、假设的英文 ID（H1/H2/H3）
"""


# ============================================================
# 3. 核心函数
# ============================================================

def critique(
    hypotheses: List[Dict[str, Any]],
    round_label: str = "V1",
    prev_scores: Optional[Dict[str, float]] = None,
    max_retries: int = 3
) -> CriticOutput:
    """
    执行评审

    Args:
        hypotheses: 假设列表
        round_label: 当前轮次标签（V1/V2/V3）
        prev_scores: 上一轮五维评分（迭代评审时传入）
        max_retries: 最大重试次数

    Returns:
        CriticOutput: 包含评分、缺陷、反事实条件

    Raises:
        RuntimeError: 超过最大重试次数仍失败
    """
    structured_llm = llm.with_structured_output(CriticOutput)

    hypotheses_str = json.dumps(hypotheses, ensure_ascii=False, indent=2)

    # 迭代评审上下文：告诉 Critic 本轮应比上一轮更好
    iterative_context = ""
    if round_label != "V1" and prev_scores:
        prev_total = sum(prev_scores.values()) / len(prev_scores) if prev_scores else 0
        weak_dims = sorted(prev_scores.items(), key=lambda x: x[1])[:2]
        weak_desc = ", ".join([f"{d}({v})" for d, v in weak_dims])

        iterative_context = f"""
【迭代评审上下文】
当前是 {round_label} 迭代评审。上一轮（V{int(round_label[1:])-1}）五维评分为：
- evidence: {prev_scores.get('evidence', 'N/A')}
- falsifiability: {prev_scores.get('falsifiability', 'N/A')}
- consistency: {prev_scores.get('consistency', 'N/A')}
- novelty: {prev_scores.get('novelty', 'N/A')}
- cross_domain: {prev_scores.get('cross_domain', 'N/A')}
- 上轮均分: {prev_total:.1f}

## 评分原则（最高优先级）
1. **关注相对改进而非绝对分数**：重点评估本轮相比上一轮的改进幅度，而非单纯追求高分。如果某维度已有改善（哪怕幅度不大），应在评分中体现正向变化。
2. **保护已有优势维度**：上一轮得分 ≥ 8 的维度，除非本轮出现严重退化，否则不应大幅扣分（降幅不超过 1.5 分）。
3. **聚焦薄弱维度突破**：上一轮最弱的两个维度（{weak_desc}）是本轮改进的重点。如果这些维度有实质性改善，即使其他维度略有波动，综合评分仍应体现进步。
4. **避免矫枉过正惩罚**：如果 Scientist 为修复某一缺陷而在其他方面做了合理权衡（如为增强可证伪性而牺牲部分新颖度），不应视为退步。
5. **评分锚定**：本轮各维度评分与上一轮的差值应在 [-2, +3] 范围内，超出此范围需在 detailed_review 中给出充分理由。
"""

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"""
## 待评审假设列表（{round_label}）
{hypotheses_str}
{iterative_context}
请对以上假设进行严格评审，按 JSON 格式输出评分、缺陷、反事实条件和缺失证据。

注意：
1. 反事实条件（counterfactual）必须具体、极端，例如："若未来探测器精度无法达到 10^-12 量级，则该假设无法验证"
2. 缺失证据（missing_evidences）应具体到可检索的关键词
3. 评分必须客观反映假设实际质量，不得放水
4. counterfactual_severity 评估该反事实条件在现实中实现的苛刻程度（越极端越不可能，分越高）
5. counterfactual_vulnerability 评估假设在该条件下被推翻的容易程度（越脆弱，分越高）
""")
    ]

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Agent 3 评审尝试 {attempt}/{max_retries}...")
            try:
                result = structured_llm.invoke(messages)
            except Exception as structured_err:
                logger.info("结构化输出失败，降级为纯文本 JSON 解析: %s",
                            type(structured_err).__name__)
                raw = llm.invoke(messages)
                raw_text = raw.content if hasattr(raw, "content") else str(raw)
                result = parse_llm_json_to_model(raw_text, CriticOutput)

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
    round_label: str = "V1"
    prev_scores: Optional[Dict[str, float]] = None
    critic_output: Optional[CriticOutput] = None
    retry_count: int = 0
    errors: List[str] = Field(default_factory=list)


def critic_node(state: dict) -> dict:
    """LangGraph 节点函数"""
    logger.info("进入 Critic Node")

    try:
        result = critique(
            state["hypotheses"],
            round_label=state.get("round_label", "V1"),
            prev_scores=state.get("prev_scores"),
        )
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