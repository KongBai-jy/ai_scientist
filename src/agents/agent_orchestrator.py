"""
Agent 4：指挥家（Orchestrator）
职责：流水线调度 + 人在回路解析 + 全链路重跑 + 快照管理
基于 LangGraph 1.x + FastAPI
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from models.schemas import (
    ExplorerOutput, ScientistOutput, CriticOutput,
    DimensionScores, OverallScore
)
from agents.agent_explorer import explore
from agents.agent_scientist import generate_hypotheses
from agents.agent_critic import critique, calculate_overall_score
from models.database import SessionLocal, SnapshotRecord

# 加载项目根目录的 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)


# ============================================================
# 1. 配置
# ============================================================

WEIGHTS = {
    "evidence": 0.25,
    "falsifiability": 0.25,
    "consistency": 0.20,
    "novelty": 0.15,
    "cross_domain": 0.15,
}

SNAPSHOTS_PATH = os.getenv(
    "SNAPSHOTS_PATH",
    str(_PROJECT_ROOT / "snapshots")
)


# ============================================================
# 2. 综合评分与统计
# ============================================================

def calculate_granularity_stats(hypotheses: List[Dict]) -> Dict[str, int]:
    """统计计划颗粒度"""
    stats = {"L1": 0, "L2": 0, "L3": 0}
    for h in hypotheses:
        plan = h.get("plan", {})
        if plan.get("L1_conceptual"):
            stats["L1"] += 1
        if plan.get("L2_quantitative"):
            stats["L2"] += 1
        if plan.get("L3_robustness"):
            stats["L3"] += 1
    return stats


def calculate_granularity_score(stats: Dict[str, int]) -> float:
    """计算颗粒度得分（L1×1 + L2×3 + L3×6）/ 总数"""
    total = stats["L1"] + stats["L2"] + stats["L3"]
    if total == 0:
        return 0
    return round((stats["L1"] * 1 + stats["L2"] * 3 + stats["L3"] * 6) / total, 2)


# ============================================================
# 3. 核心编排函数
# ============================================================

def run_full_pipeline(
    question: str,
    feedback: Optional[str] = None,
    round_label: str = "V1"
) -> Dict[str, Any]:
    """
    执行完整流水线
    """
    logger.info(f"=" * 60)
    logger.info(f"开始执行 {round_label}")
    logger.info(f"=" * 60)

    # Step 1: Explorer
    logger.info("Step 1: 探索者执行中...")
    explorer_result = explore(question)

    # Step 2: Scientist
    logger.info("Step 2: 科学家执行中...")
    scientist_result = generate_hypotheses(
        problem_skelton=explorer_result.problem_skelton,
        evidence_list=[e.model_dump() for e in explorer_result.evidence_list],
        knowledge_gaps=explorer_result.knowledge_gaps,
        analogies=[a.model_dump() for a in explorer_result.analogies],
        feedback=feedback,
    )

    # Step 3: Critic
    logger.info("Step 3: 评审官执行中...")
    critic_result = critique(
        hypotheses=[h.model_dump() for h in scientist_result.hypotheses]
    )

    # Step 4: 计算综合得分
    overall_score = calculate_overall_score(
        critic_result.scores,
        penalty=0.0
    )

    # Step 5: 统计颗粒度
    granularity_stats = calculate_granularity_stats(
        [h.model_dump() for h in scientist_result.hypotheses]
    )
    granularity_score = calculate_granularity_score(granularity_stats)

    # Step 6: 构建快照
    snapshot = {
        "round": round_label,
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "agent_explorer": explorer_result.model_dump(),
        "agent_scientist": scientist_result.model_dump(),
        "agent_critic": critic_result.model_dump(),
        "overall_score": overall_score,
        "granularity_score": granularity_score,
        "human_feedback": [{"content": feedback}] if feedback else [],
        "granularity_stats": granularity_stats,
    }

    # Step 7: 保存快照（文件 + 数据库，数据库失败不影响主流程）
    os.makedirs(SNAPSHOTS_PATH, exist_ok=True)
    filepath = os.path.join(SNAPSHOTS_PATH, f"{round_label}.json")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as file_err:
        logger.warning(f"快照文件写入失败（不影响本次运行结果）: {file_err}")

    # 写入数据库（失败仅记录日志，不中断主流程）
    try:
        db = SessionLocal()
        record = SnapshotRecord(
            round=round_label,
            question=question,
            overall_score=overall_score,
            explorer_output=explorer_result.model_dump(),
            scientist_output=scientist_result.model_dump(),
            critic_output=critic_result.model_dump(),
            granularity_stats=granularity_stats,
            human_feedback=[{"content": feedback}] if feedback else []
        )
        db.add(record)
        db.commit()
        logger.info(f"   数据库快照已写入 (id={record.id})")
    except Exception as db_err:
        logger.warning(f"数据库快照写入失败（降级为仅保存 JSON 文件）: {db_err}")
        if 'db' in locals():
            db.rollback()
    finally:
        if 'db' in locals():
            db.close()

    logger.info(f"✅ {round_label} 完成，综合得分: {overall_score}")
    logger.info(f"   颗粒度: L1={granularity_stats['L1']}, L2={granularity_stats['L2']}, L3={granularity_stats['L3']}")

    return snapshot


def iterate_with_feedback(
    question: str,
    feedback: str,
    current_round: str
) -> Dict[str, Any]:
    """
    在人在回路反馈后执行迭代
    """
    next_round = f"V{int(current_round[1:]) + 1}"
    logger.info(f"收到反馈，触发 {next_round} 全链路重跑...")

    return run_full_pipeline(
        question=question,
        feedback=feedback,
        round_label=next_round
    )


# ============================================================
# 4. 前端图表数据接口
# ============================================================

def get_snapshot(round_label: str) -> Optional[Dict[str, Any]]:
    """读取快照"""
    filepath = os.path.join(SNAPSHOTS_PATH, f"{round_label}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_all_snapshots() -> List[Dict[str, Any]]:
    """获取所有快照"""
    snapshots = []
    if not os.path.exists(SNAPSHOTS_PATH):
        return snapshots
    for filename in sorted(os.listdir(SNAPSHOTS_PATH)):
        if filename.endswith(".json"):
            filepath = os.path.join(SNAPSHOTS_PATH, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                snapshots.append(json.load(f))
    return snapshots


def get_chart_overall() -> Dict[str, Any]:
    """获取综合得分折线图数据"""
    snapshots = get_all_snapshots()
    if not snapshots:
        return {"xAxis": [], "series": {"overall_score": [], "granularity_score": []}}
    return {
        "xAxis": [s["round"] for s in snapshots],
        "series": {
            "overall_score": [s["overall_score"] for s in snapshots],
            "granularity_score": [s.get("granularity_score", 0) for s in snapshots],
        }
    }


def get_chart_radar() -> Dict[str, Any]:
    """获取雷达图数据"""
    snapshots = get_all_snapshots()
    if not snapshots:
        return {"dimensions": [], "series": {}}

    dimensions = ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"]
    result = {
        "dimensions": dimensions,
        "series": {}
    }
    for s in snapshots:
        scores = s["agent_critic"]["scores"]
        result["series"][s["round"]] = [scores[d] for d in dimensions]
    return result


def get_chart_granularity() -> Dict[str, Any]:
    """获取颗粒度堆叠图数据"""
    snapshots = get_all_snapshots()
    if not snapshots:
        return {"xAxis": [], "L1": [], "L2": [], "L3": []}
    return {
        "xAxis": [s["round"] for s in snapshots],
        "L1": [s["granularity_stats"]["L1"] for s in snapshots],
        "L2": [s["granularity_stats"]["L2"] for s in snapshots],
        "L3": [s["granularity_stats"]["L3"] for s in snapshots],
    }


def get_chart_waterfall() -> Dict[str, Any]:
    """获取缺陷修复瀑布图数据"""
    snapshots = get_all_snapshots()
    if len(snapshots) < 2:
        return {"start_score": 0, "steps": [], "end_score": 0}

    result = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i-1]
        curr = snapshots[i]
        delta = round(curr["overall_score"] - prev["overall_score"], 2)

        # 获取本轮修复的缺陷（从 Critic 的 top_flaw 和 feedback 推断）
        critic = curr["agent_critic"]
        step = {
            "label": critic.get("top_flaw", "")[:20] + "...",
            "delta": delta,
            "from_round": prev["round"],
            "to_round": curr["round"]
        }
        result.append(step)

    return {
        "start_score": snapshots[0]["overall_score"],
        "steps": result,
        "end_score": snapshots[-1]["overall_score"]
    }


def get_chart_risk() -> Dict[str, Any]:
    """获取反事实风险收敛图数据"""
    snapshots = get_all_snapshots()
    if not snapshots:
        return {"xAxis": [], "risk_index": [], "level": []}

    # 根据 counterfactual 长度粗略估算风险指数（越短说明越苛刻=风险越高）
    risk_levels = []
    for s in snapshots:
        cf = s["agent_critic"].get("counterfactual", "")
        # 简单启发：长度越短风险越高（更苛刻的条件）
        risk = max(0, min(10, 10 - len(cf) / 20))
        risk_levels.append(round(risk, 2))

    return {
        "xAxis": [s["round"] for s in snapshots],
        "risk_index": risk_levels,
        "level": ["高危" if r > 6 else "中危" if r > 3 else "低危" for r in risk_levels]
    }


# ============================================================
# 5. LangGraph 编排
# ============================================================

class OrchestratorState(BaseModel):
    question: str = ""
    feedback: Optional[str] = None
    current_round: str = "V1"
    max_rounds: int = 3

    explorer_output: Optional[ExplorerOutput] = None
    scientist_output: Optional[ScientistOutput] = None
    critic_output: Optional[CriticOutput] = None

    overall_score: float = 0.0
    snapshot: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
    retry_count: int = 0


def orchestrator_node(state: dict) -> dict:
    """Orchestrator 主节点"""
    logger.info(f"Orchestrator 执行 {state['current_round']}")

    try:
        snapshot = run_full_pipeline(
            question=state["question"],
            feedback=state.get("feedback"),
            round_label=state["current_round"]
        )

        return {
            "snapshot": snapshot,
            "overall_score": snapshot["overall_score"],
            "explorer_output": snapshot["agent_explorer"],
            "scientist_output": snapshot["agent_scientist"],
            "critic_output": snapshot["agent_critic"],
            "errors": [],
            "retry_count": 0
        }
    except Exception as e:
        logger.error(f"Orchestrator 失败: {e}")
        return {
            "errors": [str(e)],
            "retry_count": state.get("retry_count", 0) + 1
        }


def build_orchestrator_graph():
    """构建完整编排图"""
    workflow = StateGraph(OrchestratorState)

    workflow.add_node("orchestrator", orchestrator_node)
    workflow.set_entry_point("orchestrator")

    def should_continue(state: dict) -> Literal["orchestrator", "__end__"]:
        if state.get("errors") and state.get("retry_count", 0) < 3:
            logger.info(f"Orchestrator 重试: {state['retry_count']}/3")
            return "orchestrator"
        return "__end__"

    workflow.add_conditional_edges(
        "orchestrator",
        should_continue,
        {
            "orchestrator": "orchestrator",
            "__end__": END
        }
    )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)