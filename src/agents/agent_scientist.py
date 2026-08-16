"""
Agent 2：科学家（Scientist）
职责：生成竞争性假设 + 三级研究计划 + 双判定标准
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
    ScientistInput, ScientistOutput,
    Hypothesis, Plan, VerificationCriteria
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
    temperature=0.7,
    max_tokens=4096,
    timeout=180.0,
)


# ============================================================
# 2. Prompt 模板
# ============================================================

SYSTEM_PROMPT = """你是一位顶尖的跨学科科学家，专精于将文献证据转化为可验证的科学假设与研究计划。

## 核心约束（违反将导致重生成）
1. **可证伪性硬约束**：每条假设必须包含具体的、可操作的推翻条件（falsification_condition），长度≥15字符。
2. **三级计划跃迁**：每条假设的 plan 必须包含 L1（概念级）、L2（量化指标级，含数字阈值）、L3（容错级，含备选方案）。
3. **双判定标准**：verification_criteria 必须包含 confirm（成立条件）和 reject（推翻条件）。

## 假设生成策略
- 2-3 条假设应呈现竞争性：不同机制、不同尺度、不同因果关系方向
- 如果证据不足，可利用跨域类比线索进行合理推测，但必须明确标注

## 输出格式（纯 JSON，字段名必须严格一致，大小写敏感）
必须使用嵌套对象结构！禁止把对象写成描述性字符串！

【字段 Schema】
- hypotheses[i].plan                 → 对象，必须包含三个键：
    * L1_conceptual  (字符串)        → 概念级方向描述
    * L2_quantitative (字符串)       → 量化指标级，必须包含具体数字阈值（如 ≥、≤、%、K 等）
    * L3_robustness   (字符串)       → 容错级，含备选方案+对照设计
- hypotheses[i].verification_criteria → 对象，必须包含两个键：
    * confirm   (字符串)              → 假设成立需要满足的可观测条件
    * reject    (字符串)              → 假设推翻需要满足的可观测条件
- hypotheses[i].id                   → 严格为 "H1"、"H2" 或 "H3"（字符串）
- hypotheses[i].falsification_condition → 字符串，长度 ≥ 15，明确说明"在什么条件被观测到即推翻该假设"
- cross_hypothesis_comparison        → 字符串，≥20 字符，比较所有假设的机制差异

【正确示例】
{
  "hypotheses": [
    {
      "id": "H1",
      "statement": "完整的假设陈述",
      "source": "基于哪些文献/证据得出",
      "supporting_reasoning": "支持该假设的推论逻辑",
      "falsification_condition": "在 XX 条件下，观测到 Y 结果（如 p > 0.05），则该假设被推翻",
      "plan": {
        "L1_conceptual": "概念级方向描述",
        "L2_quantitative": "具体数值阈值，如敲除基因 X 后 Y 蛋白下降 > 30%",
        "L3_robustness": "若主要方法失效，采用备选方案 B，设置对照 C"
      },
      "verification_criteria": {
        "confirm": "假设成立需满足的条件（含具体阈值）",
        "reject": "假设推翻需满足的条件"
      }
    }
  ],
  "cross_hypothesis_comparison": "各假设之间的差异与互补关系"
}

【严禁出现】（出现将强制重生成）
- "plan": "L1_conceptual: ..., L2_quantitative: ..."  —— 这是字符串，不是对象
- "verification_criteria": "confirm: ..., reject: ..."  —— 这是字符串，不是对象
"""


# ============================================================
# 3. 核心函数
# ============================================================

def generate_hypotheses(
    problem_skelton: str,
    evidence_list: List[Dict[str, str]],
    knowledge_gaps: List[str],
    analogies: List[Dict[str, str]],
    feedback: Optional[str] = None,
    max_retries: int = 3
) -> ScientistOutput:
    """
    生成假设与研究计划

    Args:
        problem_skelton: 问题骨架
        evidence_list: 证据列表
        knowledge_gaps: 知识缺口
        analogies: 跨域类比线索
        feedback: 专家反馈（迭代时传入）
        max_retries: 最大重试次数

    Returns:
        ScientistOutput: 包含假设和研究计划

    Raises:
        RuntimeError: 超过最大重试次数仍失败
    """
    structured_llm = llm.with_structured_output(ScientistOutput)

    evidence_str = json.dumps(evidence_list, ensure_ascii=False, indent=2)
    gaps_str = json.dumps(knowledge_gaps, ensure_ascii=False, indent=2)
    analogies_str = json.dumps(analogies, ensure_ascii=False, indent=2)

    feedback_section = f"\n## 专家反馈（本轮迭代必须响应的修正指令）\n{feedback}" if feedback else ""

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"""
## 问题骨架
{problem_skelton}

## 证据列表
{evidence_str}

## 知识缺口
{gaps_str}

## 跨域类比线索
{analogies_str}
{feedback_section}

请严格按照 JSON 格式输出。
""")
    ]

    last_error = None
    current_temp = 0.7

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Agent 2 生成尝试 {attempt}/{max_retries}...")

            # 每次重试微调 temperature
            if attempt > 1:
                llm_cur = ChatOpenAI(
                    model=os.getenv("QWEN_MODEL", "qwen-plus"),
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                    base_url=os.getenv("DASHSCOPE_API_BASE", _DEFAULT_API_BASE),
                    temperature=current_temp,
                    max_tokens=4096,
                    timeout=180.0,
                )
            else:
                llm_cur = llm

            try:
                # 首选路径：结构化输出（如果 API 网关支持 response_format）
                structured_llm = llm_cur.with_structured_output(ScientistOutput)
                result = structured_llm.invoke(messages)
            except Exception as structured_err:
                # 降级路径：纯文本 JSON → 手动解析（兼容自建/专有云网关不支持 response_format）
                logger.info("结构化输出失败，降级为纯文本 JSON 解析: %s",
                            type(structured_err).__name__)
                raw = llm_cur.invoke(messages)
                raw_text = raw.content if hasattr(raw, "content") else str(raw)
                result = parse_llm_json_to_model(raw_text, ScientistOutput)

            # 额外校验：每条假设的可证伪条件长度
            for h in result.hypotheses:
                if len(h.falsification_condition) < 15:
                    raise ValueError(f"假设 {h.id} 的可证伪条件过短 ({len(h.falsification_condition)} < 15)")

                # 校验 plan 是否完整
                plan = h.plan
                if not plan.L1_conceptual or not plan.L2_quantitative or not plan.L3_robustness:
                    raise ValueError(f"假设 {h.id} 的 plan 缺少 L1/L2/L3")

            logger.info(f"✅ Agent 2 生成 {len(result.hypotheses)} 条假设")
            return result

        except Exception as e:
            logger.warning(f"第 {attempt} 次失败: {e}")
            last_error = e
            current_temp = max(0.3, current_temp - 0.1)

            if attempt < max_retries:
                # 将错误信息注入反馈
                error_hint = (
                    f"【上一轮失败原因】{str(e)}\n"
                    "请务必严格遵守以下两条硬格式要求，不得用字符串替代对象：\n"
                    "1) plan 必须是对象，包含 L1_conceptual、L2_quantitative、L3_robustness 三个键\n"
                    "2) verification_criteria 必须是对象，包含 confirm、reject 两个键\n"
                    "示例正确写法: \"plan\": {\"L1_conceptual\": \"...\", \"L2_quantitative\": \"...\", \"L3_robustness\": \"...\"}\n"
                    "示例错误写法: \"plan\": \"L1_conceptual: ..., L2_quantitative: ...\"（这是字符串！）"
                )
                messages = [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=f"""
## 问题骨架
{problem_skelton}

## 证据列表
{evidence_str}

## 知识缺口
{gaps_str}

## 跨域类比线索
{analogies_str}

## 专家反馈（含上一轮错误修正）
{error_hint}

请严格按照 JSON 格式输出。
""")
                ]
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Agent 2 在 {max_retries} 次尝试后失败: {last_error}")


# ============================================================
# 4. LangGraph 节点
# ============================================================

class ScientistState(BaseModel):
    problem_skelton: str = ""
    evidence_list: List[Dict[str, str]] = Field(default_factory=list)
    knowledge_gaps: List[str] = Field(default_factory=list)
    analogies: List[Dict[str, str]] = Field(default_factory=list)
    feedback: Optional[str] = None
    scientist_output: Optional[ScientistOutput] = None
    retry_count: int = 0
    errors: List[str] = Field(default_factory=list)


def scientist_node(state: dict) -> dict:
    """LangGraph 节点函数"""
    logger.info("进入 Scientist Node")

    try:
        result = generate_hypotheses(
            problem_skelton=state["problem_skelton"],
            evidence_list=state["evidence_list"],
            knowledge_gaps=state["knowledge_gaps"],
            analogies=state["analogies"],
            feedback=state.get("feedback"),
        )
        return {
            "scientist_output": result.model_dump(),
            "retry_count": 0,
            "errors": []
        }
    except Exception as e:
        logger.error(f"Scientist Node 失败: {e}")
        return {
            "scientist_output": None,
            "retry_count": state.get("retry_count", 0) + 1,
            "errors": [str(e)]
        }


def build_scientist_graph():
    """构建 Scientist 子图"""
    workflow = StateGraph(ScientistState)

    workflow.add_node("scientist", scientist_node)
    workflow.set_entry_point("scientist")

    def should_continue(state: dict) -> str:
        if state.get("errors") and state.get("retry_count", 0) < 3:
            return "scientist"
        return "__end__"

    workflow.add_conditional_edges(
        "scientist",
        should_continue,
        {
            "scientist": "scientist",
            "__end__": END
        }
    )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)