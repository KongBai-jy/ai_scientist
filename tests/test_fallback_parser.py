"""
最小单元测试：模拟 LLM 把嵌套对象写成字符串时，fallback 工具能否正确对齐。
不依赖 API Key，可离线直接运行：
  python -m tests.test_fallback_parser
"""
import json
import sys
from pathlib import Path

# 让脚本直接跑也能 import utils
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.llm_structured_fallback import parse_llm_json_to_model
from models.schemas import ScientistOutput, CriticOutput, ExplorerOutput


# ---------- 测试 1：Scientist 报错截图里的典型错误（plan/verification_criteria 是字符串）----------
BAD_SCIENTIST_RESPONSE = """
这是我给你的假设：
```json
{
  "hypotheses": [
    {
      "id": "H1",
      "statement": "掺杂 BaZrO3 人工钉扎中心可在保持 Tc 不变前提下使临界电流 Jc 提升 3 倍以上",
      "source": "MacManus-Driscoll et al., 2004, Nat. Mater.",
      "supporting_reasoning": "从钉扎中心与超导序参量解耦的理论出发",
      "falsification_condition": "若在 77K 自场下测得 Jc 提升不到 30%，即推翻该假设",
      "plan": "L1_conceptual: 通过人工引入化学不相容的 BaZrO3 纳米相制造磁通钉扎中心\\nL2_quantitative: 77K 自场下临界电流密度 Jc 提升 ≥ 200%\\nL3_robustness: 若 BaZrO3 导致过度晶格畸变，则改用 BaSnO3 替代方案，同时制备纯 YBCO 对照组",
      "verification_criteria": "confirm: 77K 自场 Jc 对比纯 YBCO 提升 ≥ 200% 且 ΔTc ≤ 2K\\nreject: Jc 提升 < 30% 或 ΔTc > 5K"
    },
    {
      "id": "H2",
      "statement": "高压（≤15GPa）优化 CuO2 面键长可将最优 Tc 推至 105K 以上",
      "source": "Almasan et al., 1992, Phys. Rev. B",
      "supporting_reasoning": "从相变硬约束条件推断压力对关联长度的优化",
      "falsification_condition": "若 10GPa 下 Tc < 95K 且在所有氧掺杂样品上均无增压趋势则推翻",
      "plan": "conceptual_direction: 流体静压力调控 Cu-O 键长以逼近铜氧面最优键角",
      "verification_criteria": "criteria_for_hypothesis_evaluation: 高压磁阻测量验证 Tc 变化"
    }
  ],
  "cross_hypothesis_comparison": "H1 聚焦磁通钉扎/Jc，H2 聚焦 Tc 本身，二者正交互补，但 H2 加压技术难度高、H1 更易工程化实现"
}
```
"""


def test_scientist_bad_response():
    print("=" * 60)
    print("Test 1: Scientist 字符串对象响应")
    print("=" * 60)
    out = parse_llm_json_to_model(BAD_SCIENTIST_RESPONSE, ScientistOutput)

    print(f"  hypotheses 数量: {len(out.hypotheses)}")
    for h in out.hypotheses:
        print(f"  [{h.id}] plan 对象类型? {type(h.plan).__name__}")
        print(f"       plan.L1={h.plan.L1_conceptual[:30]}...")
        print(f"       plan.L2={h.plan.L2_quantitative[:30]}...")
        print(f"       plan.L3={h.plan.L3_robustness[:30]}...")
        print(f"       vc 对象类型? {type(h.verification_criteria).__name__}")
        print(f"       vc.confirm={h.verification_criteria.confirm[:30]}...")
        print(f"       vc.reject={h.verification_criteria.reject[:30]}...")
        assert isinstance(h.plan, object) and hasattr(h.plan, "L2_quantitative"), "plan 必须还原为对象"
        assert hasattr(h.verification_criteria, "reject"), "vc 必须还原为对象"
    print("PASS\n")


# ---------- 测试 2：Critic scores 写成字符串 ----------
BAD_CRITIC_RESPONSE = """
{
  "scores": "evidence: 7.5; falsifiability: 6.0; consistency: 8.0; novelty: 7.0; cross_domain: 6.5",
  "top_flaw": "Top flaw description with enough characters here",
  "counterfactual": "In some impossible extreme condition the hypothesis fails clearly",
  "missing_evidences": ["Ev1", "Ev2"],
  "detailed_review": "This is a very detailed review that has more than 30 characters for sure."
}
"""


def test_critic_bad_response():
    print("=" * 60)
    print("Test 2: Critic scores 字符串响应")
    print("=" * 60)
    out = parse_llm_json_to_model(BAD_CRITIC_RESPONSE, CriticOutput)
    s = out.scores
    print(f"  scores type: {type(s).__name__}")
    print(f"  evidence={s.evidence}, falsifiability={s.falsifiability}, consistency={s.consistency}")
    print(f"  novelty={s.novelty}, cross_domain={s.cross_domain}")
    # 兜底策略会把字符串 scores 变成 0 值（因为 parse 不出来），但 Pydantic 不应报错
    assert isinstance(s.evidence, (int, float))
    print("PASS\n")


# ---------- 测试 3：Explorer problem_skeleton 拼写错 ----------
BAD_EXPLORER_RESPONSE = """
{
  "problem_skeleton": "这是 spell error 的骨架：微观相互作用到宏观涌现",
  "evidence_list": [
    {"claim": "Claim one", "source": "Source A"}
  ],
  "knowledge_gap": "这应该是列表",
  "analogies": [
    {"field": "Physics", "phenomenon": "Ising model", "mapping": "Local interactions -> global order"}
  ]
}
"""


def test_explorer_bad_response():
    print("=" * 60)
    print("Test 3: Explorer 字段名拼写错误")
    print("=" * 60)
    out = parse_llm_json_to_model(BAD_EXPLORER_RESPONSE, ExplorerOutput)
    print(f"  problem_skelton length: {len(out.problem_skelton)}")
    print(f"  evidence_list count: {len(out.evidence_list)}")
    print(f"  knowledge_gaps type: {type(out.knowledge_gaps).__name__}, count={len(out.knowledge_gaps)}")
    print(f"  analogies count: {len(out.analogies)}")
    if out.analogies:
        print(f"  analogy[0].mapping_relation: {out.analogies[0].mapping_relation}")
    assert len(out.problem_skelton) >= 5
    print("PASS\n")


if __name__ == "__main__":
    test_scientist_bad_response()
    test_critic_bad_response()
    test_explorer_bad_response()
    print("=" * 60)
    print("✅  全部 3 个 fallback 解析测试通过")
    print("=" * 60)
