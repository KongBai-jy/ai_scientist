"""
共享 Pydantic 数据模型
所有 Agent 共用
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator
import re


# ============================================================
# Agent 1：探索者 输入/输出
# ============================================================

class Evidence(BaseModel):
    claim: str = Field(..., min_length=5, description="证据陈述")
    source: str = Field(..., min_length=3, description="文献来源")
    year: Optional[str] = Field(None, description="发表年份")


class Analogy(BaseModel):
    field: str = Field(..., min_length=2, description="来源学科")
    phenomenon: str = Field(..., min_length=5, description="类比现象")
    mapping_relation: str = Field(..., min_length=5, description="到本问题的映射关系")


class ExplorerInput(BaseModel):
    question: str = Field(..., min_length=5, description="用户科学问题")


class ExplorerOutput(BaseModel):
    problem_skelton: str = Field(..., min_length=5, description="问题骨架")
    evidence_list: List[Evidence] = Field(default_factory=list, description="证据列表")
    knowledge_gaps: List[str] = Field(default_factory=list, description="知识缺口")
    analogies: List[Analogy] = Field(default_factory=list, description="跨域类比线索")


# ============================================================
# Agent 2：科学家 输入/输出
# ============================================================

class Plan(BaseModel):
    L1_conceptual: str = Field(..., min_length=5, description="概念级方向")
    L2_quantitative: str = Field(..., min_length=5, description="量化指标级，含数值阈值")
    L3_robustness: str = Field(..., min_length=10, description="容错级，备选方案与对照设计")

    @field_validator("L2_quantitative")
    @classmethod
    def validate_quantitative(cls, v: str) -> str:
        if not re.search(r"\d+", v):
            raise ValueError("L2_quantitative 必须包含具体的数值/百分比阈值")
        return v


class VerificationCriteria(BaseModel):
    confirm: str = Field(..., min_length=10, description="假设成立需满足的条件")
    reject: str = Field(..., min_length=10, description="假设推翻需满足的条件")


class Hypothesis(BaseModel):
    id: str = Field(..., pattern=r"^H[1-3]$", description="假设编号 H1/H2/H3")
    statement: str = Field(..., min_length=10, description="假设完整陈述")
    source: str = Field(..., min_length=5, description="基于哪些文献/证据得出")
    supporting_reasoning: str = Field(..., min_length=10, description="支持该假设的推论逻辑")
    falsification_condition: str = Field(..., min_length=15, description="可证伪条件")
    plan: Plan
    verification_criteria: VerificationCriteria


class ScientistInput(BaseModel):
    problem_skelton: str
    evidence_list: List[Evidence]
    knowledge_gaps: List[str]
    analogies: List[Analogy]
    feedback: Optional[str] = None


class ScientistOutput(BaseModel):
    hypotheses: List[Hypothesis] = Field(..., min_length=2, max_length=3)
    cross_hypothesis_comparison: str = Field(..., min_length=20)


# ============================================================
# Agent 3：评审官 输入/输出
# ============================================================

class DimensionScores(BaseModel):
    evidence: float = Field(..., ge=0, le=10)
    falsifiability: float = Field(..., ge=0, le=10)
    consistency: float = Field(..., ge=0, le=10)
    novelty: float = Field(..., ge=0, le=10)
    cross_domain: float = Field(..., ge=0, le=10)


class CriticInput(BaseModel):
    hypotheses: List[Hypothesis]


class CriticOutput(BaseModel):
    scores: DimensionScores
    top_flaw: str = Field(..., min_length=10)
    counterfactual: str = Field(..., min_length=15)
    missing_evidences: List[str] = Field(default_factory=list)
    detailed_review: str = Field(..., min_length=30)


# ============================================================
# 综合评分
# ============================================================

class Weights(BaseModel):
    evidence: float = 0.25
    falsifiability: float = 0.25
    consistency: float = 0.20
    novelty: float = 0.15
    cross_domain: float = 0.15


class OverallScore(BaseModel):
    overall_score: float
    penalty: float = 0.0
    dimension_scores: DimensionScores


# ============================================================
# 快照
# ============================================================

class Snapshot(BaseModel):
    round: str
    timestamp: str
    question: str
    agent_explorer: ExplorerOutput
    agent_scientist: ScientistOutput
    agent_critic: CriticOutput
    overall_score: float
    human_feedback: List[Dict[str, Any]] = Field(default_factory=list)
    granularity_stats: Dict[str, float] = Field(default_factory=dict)