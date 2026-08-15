"""
输出校验工具
"""

import re
from typing import List, Dict, Any
from models.schemas import ScientistOutput, CriticOutput


def validate_scientist_output(data: Dict[str, Any]) -> List[str]:
    """
    校验 Scientist 输出
    返回错误列表
    """
    errors = []

    hypotheses = data.get("hypotheses", [])
    if not (2 <= len(hypotheses) <= 3):
        errors.append(f"假设数量应为 2-3 条，实际为 {len(hypotheses)} 条")

    for i, h in enumerate(hypotheses):
        hid = h.get("id", f"H{i+1}")

        # 校验 falsification_condition
        fc = h.get("falsification_condition", "")
        if len(fc) < 15:
            errors.append(f"假设 {hid} 的可证伪条件过短 ({len(fc)} < 15)")

        # 校验 plan
        plan = h.get("plan", {})
        for level in ["L1_conceptual", "L2_quantitative", "L3_robustness"]:
            if not plan.get(level):
                errors.append(f"假设 {hid} 的 plan 缺少 {level}")

        # 校验 L2 是否包含数字
        if plan.get("L2_quantitative") and not re.search(r"\d+", plan["L2_quantitative"]):
            errors.append(f"假设 {hid} 的 L2_quantitative 必须包含数值阈值")

        # 校验 verification_criteria
        vc = h.get("verification_criteria", {})
        for key in ["confirm", "reject"]:
            if not vc.get(key) or len(vc[key]) < 10:
                errors.append(f"假设 {hid} 的 verification_criteria 缺少 {key}")

    return errors


def validate_critic_output(data: Dict[str, Any]) -> List[str]:
    """
    校验 Critic 输出
    返回错误列表
    """
    errors = []

    scores = data.get("scores", {})
    for dim in ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"]:
        val = scores.get(dim)
        if val is None:
            errors.append(f"缺少评分维度: {dim}")
        elif not (0 <= val <= 10):
            errors.append(f"评分 {dim}={val} 超出 0-10 范围")

    if len(data.get("top_flaw", "")) < 10:
        errors.append("top_flaw 长度不足 10")

    if len(data.get("counterfactual", "")) < 15:
        errors.append("counterfactual 长度不足 15")

    return errors