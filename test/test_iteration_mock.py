"""
Mock 迭代测试脚本：验证质量加权颗粒度机制
==========================================
使用 V1 快照数据，分别计算 V1 和模拟 V2 的颗粒度质量得分，
验证质量加权后分数能否在迭代中产生实际差异。

运行：python test/test_iteration_mock.py
"""

import json
import os
import re
import random
from pathlib import Path

random.seed(42)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_PATH = os.getenv("SNAPSHOTS_PATH", str(_PROJECT_ROOT / "snapshots"))


def _l1_quality(text: str) -> float:
    if len(text) < 10:
        return 0.0
    score = 1.0
    score += min(1.0, len(text) / 100)
    keywords = ["方法", "模型", "算法", "框架", "机制", "结构", "模块", "分解", "学习", "推理", "映射", "变换"]
    kw_count = sum(1 for kw in keywords if kw in text)
    score += min(1.0, kw_count * 0.15)
    return min(3.0, round(score, 2))


def _l2_quality(text: str) -> float:
    if not text or not re.search(r'\d+', text) or len(text) < 15:
        return 0.0
    score = 1.0
    num_count = len(re.findall(r'\d+', text))
    if num_count >= 5:
        score += 1.0
    elif num_count >= 3:
        score += 0.5
    score += min(1.0, len(text) / 80)
    return min(3.0, round(score, 2))


def _l3_quality(text: str) -> float:
    if not text or len(text) < 20:
        return 0.0
    risk_kws = ["若", "如果", "万一", "一旦", "假设", "alternative", "fallback"]
    mitigate_kws = ["备选", "替代", "对照", "切换", "转换", "降级", "补救"]
    risk_count = sum(1 for kw in risk_kws if kw in text)
    mitigate_count = sum(1 for kw in mitigate_kws if kw in text)
    if risk_count == 0:
        return 0.0
    score = 1.0
    if risk_count >= 2:
        score += 0.5
    if mitigate_count >= 2:
        score += 0.5
    score += min(1.0, len(text) / 100)
    return min(3.0, round(score, 2))


def calc_granularity_stats(hypotheses: list) -> dict:
    stats = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    for h in hypotheses:
        plan = h.get("plan", {})
        stats["L1"] += _l1_quality(plan.get("L1_conceptual", ""))
        stats["L2"] += _l2_quality(plan.get("L2_quantitative", ""))
        stats["L3"] += _l3_quality(plan.get("L3_robustness", ""))
    return {k: round(v, 2) for k, v in stats.items()}


def calc_granularity_score(stats: dict) -> float:
    MAX_POSSIBLE = 3 * 3.0 * 10
    weighted_sum = stats["L1"] * 1 + stats["L2"] * 3 + stats["L3"] * 6
    return round((weighted_sum / MAX_POSSIBLE) * 6, 2)


def analyze_plan_quality(name: str, plan: dict):
    l1 = plan.get("L1_conceptual", "")
    l2 = plan.get("L2_quantitative", "")
    l3 = plan.get("L3_robustness", "")

    q1 = _l1_quality(l1)
    q2 = _l2_quality(l2)
    q3 = _l3_quality(l3)

    print(f"\n  {name}:")
    print(f"    L1: 长度={len(l1):>3} 字符 | 质量分={q1:.2f}/3 | {'✅' if q1 > 0 else '❌'}")
    if q1 > 0:
        kws = ["方法", "模型", "算法", "框架", "机制", "结构", "模块", "分解", "学习", "推理", "映射", "变换"]
        found = [k for k in kws if k in l1]
        print(f"      命中关键词 {len(found)} 个: {found[:6]}...")

    print(f"    L2: 长度={len(l2):>3} 字符 | 数字={len(re.findall(r'\d+', l2))} 个 | 质量分={q2:.2f}/3 | {'✅' if q2 > 0 else '❌'}")

    risk_kws = ["若", "如果", "万一", "一旦", "假设", "alternative", "fallback"]
    mitigate_kws = ["备选", "替代", "对照", "切换", "转换", "降级", "补救"]
    r_found = [k for k in risk_kws if k in l3]
    m_found = [k for k in mitigate_kws if k in l3]
    print(f"    L3: 长度={len(l3):>3} 字符 | 风险词={len(r_found)} 个 | 缓解词={len(m_found)} 个 | 质量分={q3:.2f}/3 | {'✅' if q3 > 0 else '❌'}")

    return q1, q2, q3


def main():
    print("=" * 70)
    print("  质量加权颗粒度机制验证")
    print("=" * 70)

    # 加载 V1 数据
    v1_path = os.path.join(SNAPSHOTS_PATH, "V1.json")
    if not os.path.exists(v1_path):
        print(f"❌ V1 快照不存在: {v1_path}")
        return
    with open(v1_path, "r", encoding="utf-8") as f:
        v1 = json.load(f)

    hypotheses = v1["agent_scientist"]["hypotheses"]

    # ========== Part 1: V1 原始计划质量分析 ==========
    print("\n" + "-" * 50)
    print("  Part 1: V1 假设计划质量分析")
    print("-" * 50)

    v1_quality_per_h = []
    for h in hypotheses:
        q1, q2, q3 = analyze_plan_quality(h["id"], h["plan"])
        v1_quality_per_h.append((q1, q2, q3))

    v1_stats = calc_granularity_stats(hypotheses)
    v1_score = calc_granularity_score(v1_stats)

    print(f"\n  V1 颗粒度统计:")
    print(f"    L1 总计: {v1_stats['L1']:.2f} (旧: 3)")
    print(f"    L2 总计: {v1_stats['L2']:.2f} (旧: 3)")
    print(f"    L3 总计: {v1_stats['L3']:.2f} (旧: 3)")
    print(f"    颗粒度得分: {v1_score:.2f} (旧: 3.33)")

    # ========== Part 2: 模拟 V2 改进后质量分析 ==========
    print("\n" + "-" * 50)
    print("  Part 2: 模拟 V2 迭代改进后质量分析")
    print("-" * 50)

    # 模拟 V2 改进：假设科学家根据反馈增强了各 plan 内容
    v2_hypotheses = []
    for h in hypotheses:
        plan = h["plan"]
        new_plan = {}

        # L1: 增加关键词和长度
        l1 = plan["L1_conceptual"]
        new_plan["L1_conceptual"] = l1 + " 该方法结合了流形学习的拓扑保持映射与模块分解机制，通过逐层特征学习实现因果结构的稳健推理。"

        # L2: 增加更多数字和更长描述
        l2 = plan["L2_quantitative"]
        new_plan["L2_quantitative"] = l2 + " 当 n≥500 时 bootstrap 置信区间覆盖率 ≥ 95%，ATE 估计 MSE ≤ 0.08，统计功效 1-β ≥ 0.90，Cohen's d ≥ 0.5。"

        # L3: 增加更多风险词和缓解词
        l3 = plan["L3_robustness"]
        new_plan["L3_robustness"] = l3 + " 若主方案失败，则切换至备选路径 B；如果精度不达标，启用对照实验 C；万一出现分布偏移，降级使用鲁棒 IPW 估计器作为 fallback 补救方案。"

        new_h = {**h, "plan": new_plan}
        v2_hypotheses.append(new_h)

    v2_quality_per_h = []
    for h in v2_hypotheses:
        q1, q2, q3 = analyze_plan_quality(h["id"], h["plan"])
        v2_quality_per_h.append((q1, q2, q3))

    v2_stats = calc_granularity_stats(v2_hypotheses)
    v2_score = calc_granularity_score(v2_stats)

    print(f"\n  V2 颗粒度统计:")
    print(f"    L1 总计: {v2_stats['L1']:.2f} (旧: 3)")
    print(f"    L2 总计: {v2_stats['L2']:.2f} (旧: 3)")
    print(f"    L3 总计: {v2_stats['L3']:.2f} (旧: 3)")
    print(f"    颗粒度得分: {v2_score:.2f} (旧: 3.33)")

    # ========== Part 3: 对比 ==========
    print("\n" + "=" * 70)
    print("  Part 3: V1 → V2 颗粒度对比")
    print("=" * 70)

    print(f"\n  {'指标':<16} {'V1':>10} {'V2':>10} {'变化':>10}")
    print("  " + "-" * 48)
    print(f"  {'L1 总计':<14} {v1_stats['L1']:>10.2f} {v2_stats['L1']:>10.2f} {v2_stats['L1']-v1_stats['L1']:>+10.2f}")
    print(f"  {'L2 总计':<14} {v1_stats['L2']:>10.2f} {v2_stats['L2']:>10.2f} {v2_stats['L2']-v1_stats['L2']:>+10.2f}")
    print(f"  {'L3 总计':<14} {v1_stats['L3']:>10.2f} {v2_stats['L3']:>10.2f} {v2_stats['L3']-v1_stats['L3']:>+10.2f}")
    print("  " + "-" * 48)
    score_delta = v2_score - v1_score
    arrow = "↑" if score_delta > 0 else ("↓" if score_delta < 0 else "→")
    print(f"  {'颗粒度得分':<14} {v1_score:>10.2f} {v2_score:>10.2f} {arrow} {score_delta:>+9.2f}")

    # 可视化
    print(f"\n  质量分布 (每条假设):")
    print(f"  {'假设':<6} {'V1 L1':>8} {'V1 L2':>8} {'V1 L3':>8} | {'V2 L1':>8} {'V2 L2':>8} {'V2 L3':>8}")
    print("  " + "-" * 66)
    for i, ((v1q1, v1q2, v1q3), (v2q1, v2q2, v2q3)) in enumerate(zip(v1_quality_per_h, v2_quality_per_h)):
        hid = f"H{i+1}"
        print(f"  {hid:<6} {v1q1:>8.2f} {v1q2:>8.2f} {v1q3:>8.2f} | {v2q1:>8.2f} {v2q2:>8.2f} {v2q3:>8.2f}")

    # 结论
    print(f"\n  【结论】")
    if score_delta > 0:
        print(f"  ✅ 质量加权有效！颗粒度得分从 {v1_score:.2f} → {v2_score:.2f}（{arrow} {score_delta:+.2f}）")
        print(f"     旧系统：V1 和 V2 颗粒度得分均为 3.33，无法区分质量差异")
        print(f"     新系统：V1={v1_score:.2f} V2={v2_score:.2f}，能有效反映内容丰富度的提升")
    else:
        print(f"  ⚠️ 得分无变化或下降，需调整质量评分参数")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()