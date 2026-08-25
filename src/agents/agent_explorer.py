"""
Agent 1：探索者（Explorer）
职责：问题解构 + 文献检索 + 跨域类比迁移
基于 LangChain 1.x + 千问模型
"""

import json
import logging
import os
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from models.schemas import (
    ExplorerInput, ExplorerOutput,
    Evidence, Analogy
)
from services.chroma_service import ChromaService
from utils.llm_structured_fallback import parse_llm_json_to_model

# 加载项目根目录的 .env（= src/ 上一级）
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
    temperature=0.6,
    max_tokens=4096,
    timeout=180.0,
)


# ============================================================
# 2. Prompt 模板
# ============================================================

SYSTEM_PROMPT = """你是一位顶尖的科学探索者，擅长解构复杂科学问题并挖掘多学科证据。

## 核心职责
1. **问题骨架提取**：将科学问题提炼为底层逻辑结构（如"微观单元交互→宏观现象涌现"）
2. **证据挖掘**：基于提供的文献片段提取结构化证据，每条必须附来源
3. **知识缺口识别**：指出当前文献未覆盖的盲区
4. **跨域类比**：当直接文献不足时，从其他学科借用已解决的经典案例

## 跨域类比策略
当本地知识库文献不足时，请自动启动跨域类比迁移：
- 提取问题的**底层逻辑结构**（如：微观→宏观涌现、因果关系推断、模式识别等）
- 从以下学科中寻找已解决的经典案例作为类比：
  - 物理：伊辛模型、相变、熵增原理、量子纠缠
  - 生物：集群行为、进化博弈、神经网络、基因调控
  - 计算机：强化学习、图网络、信息论、复杂度理论
  - 数学：动力系统、图论、概率图模型、拓扑学

## 输出格式（纯 JSON，字段名严格一致）
- problem_skelton (字符串，≥5 字符) —— 问题骨架（注意是 skelton，不是 skeleton）
- evidence_list (数组) —— 每条必须包含 claim + source 两个字符串键，year 可选
- knowledge_gaps (字符串数组)
- analogies (数组) —— 每条必须包含 field + phenomenon + mapping_relation 三个字符串键

【正确示例】
{
  "problem_skelton": "底层逻辑结构描述",
  "evidence_list": [
    {"claim": "证据陈述", "source": "论文来源", "year": "2023"}
  ],
  "knowledge_gaps": ["缺口1", "缺口2"],
  "analogies": [
    {"field": "统计物理", "phenomenon": "伊辛模型中的局部自旋相互作用产生宏观磁化", "mapping_relation": "局部神经元同步→全局意识状态"}
  ]
}

## 约束
- 每条证据必须绑定 source，缺失则视为无效
- 若本地文献不足，必须利用跨域类比补全，不允许返回空列表

## 语言要求（强制）
- 所有输出 JSON 字段（problem_skelton、claim、knowledge_gaps、phenomenon、mapping_relation 等）**必须使用中文**
- 允许保留的英文仅限：专有名词（如 YBCO、Riemann hypothesis、DNA、CRISPR、arXiv 编号、DOI、学科标准术语）、文献来源（source）、论文标题与作者名
- 若证据片段为英文，请在 claim 中用中文转述其核心含义，保留必要的英文术语
"""


# ============================================================
# 3. 核心函数
# ============================================================

def explore(
    question: str,
    top_k: int = 5,
    max_retries: int = 3
) -> ExplorerOutput:
    """
    执行探索

    Args:
        question: 用户科学问题
        top_k: 向量检索返回文档数
        max_retries: 最大重试次数

    Returns:
        ExplorerOutput: 包含问题骨架、证据、缺口、类比

    Raises:
        RuntimeError: 超过最大重试次数仍失败
    """
    # 1. 向量检索
    chroma = ChromaService()
    results = chroma.similarity_search(question, k=top_k)

    # 2. 构建 Prompt
    if results:
        evidence_context = "\n".join([
            f"- {r['content']} (来源: {r['metadata'].get('source', '未知')})"
            for r in results
        ])
    else:
        evidence_context = "（未检索到相关文献，请基于跨域类比推理）"

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"""
## 用户问题
{question}

## 本地知识库检索结果（文献片段）
{evidence_context}

请提取问题骨架、结构化证据、知识缺口和跨域类比线索，严格按 JSON 格式输出。

注意：
1. 如果文献片段不足，请主动从物理、生物、计算机、数学等学科中寻找类比案例
2. 类比必须说明 mapping_relation（如何映射到本问题）
3. 证据列表中每条都必须有 source 字段
""")
    ]

    # 3. 调用模型（带重试）
    structured_llm = llm.with_structured_output(ExplorerOutput)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Agent 1 探索尝试 {attempt}/{max_retries}...")
            try:
                result = structured_llm.invoke(messages)
            except Exception as structured_err:
                logger.info("结构化输出失败，降级为纯文本 JSON 解析: %s",
                            type(structured_err).__name__)
                raw = llm.invoke(messages)
                raw_text = raw.content if hasattr(raw, "content") else str(raw)
                result = parse_llm_json_to_model(raw_text, ExplorerOutput)

            # 校验：证据和类比不能同时为空
            if not result.evidence_list and not result.analogies:
                raise ValueError("证据和类比均为空，需要至少一项")

            # 校验：每条证据必须有 source
            for i, ev in enumerate(result.evidence_list):
                if not ev.source or len(ev.source) < 3:
                    raise ValueError(f"证据 {i+1} 缺少有效的 source 字段")

            logger.info(f"✅ 探索完成：{len(result.evidence_list)} 条证据，{len(result.analogies)} 条类比")
            return result

        except Exception as e:
            logger.warning(f"第 {attempt} 次失败: {e}")
            last_error = e

            # 重试时降低 temperature 使输出更稳定
            if attempt < max_retries:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"探索失败: {last_error}")


# ============================================================
# 4. LangGraph 节点
# ============================================================

class ExplorerState(BaseModel):
    question: str = ""
    explorer_output: Optional[ExplorerOutput] = None
    retry_count: int = 0
    errors: List[str] = Field(default_factory=list)


def explorer_node(state: dict) -> dict:
    """LangGraph 节点函数"""
    logger.info("进入 Explorer Node")

    try:
        result = explore(state["question"])
        return {
            "explorer_output": result.model_dump(),
            "retry_count": 0,
            "errors": []
        }
    except Exception as e:
        logger.error(f"Explorer Node 失败: {e}")
        return {
            "explorer_output": None,
            "retry_count": state.get("retry_count", 0) + 1,
            "errors": [str(e)]
        }


def build_explorer_graph():
    """构建 Explorer 子图"""
    workflow = StateGraph(ExplorerState)

    workflow.add_node("explorer", explorer_node)
    workflow.set_entry_point("explorer")

    def should_continue(state: dict) -> str:
        if state.get("errors") and state.get("retry_count", 0) < 3:
            logger.info(f"重试 Explorer: {state['retry_count']}/3")
            return "explorer"
        return "__end__"

    workflow.add_conditional_edges(
        "explorer",
        should_continue,
        {
            "explorer": "explorer",
            "__end__": END
        }
    )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)