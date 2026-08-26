"""suggest_questions 端到端测试

验证三种场景：
    1. 冷启动（无 snapshot，project_id=None）→ 应能调 LLM 返回 3 条问题
    2. 带前缀（context="如何提升"）→ 3 条建议应自然延伸该前缀
    3. feedback 模式 → 返回改进方向类问题

用法：
    python test/test_suggest_questions.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from agents.agent_orchestrator import suggest_questions


def _print_result(label: str, result: dict) -> None:
    print(f"\n【{label}】")
    print(f"  based_on: {result.get('based_on')}")
    if result.get("error"):
        print(f"  error:    {result['error']}")
    qs = result.get("questions", [])
    print(f"  数量:     {len(qs)}")
    for i, q in enumerate(qs, 1):
        print(f"  [{i}] {q}  ({len(q)} 字符)")


def main():
    print("=" * 60)
    print("suggest_questions 端到端测试")
    print("=" * 60)

    # 场景 1：冷启动，无 context
    r1 = suggest_questions(context="", mode="question", project_id=None, top_k=3)
    _print_result("场景1: 冷启动（无 context，无 project_id）", r1)

    # 场景 2：带前缀
    r2 = suggest_questions(context="如何提升超导体的临界温度", mode="question", project_id=None, top_k=3)
    _print_result("场景2: 带前缀（context='如何提升超导体的临界温度'）", r2)

    # 场景 3：feedback 模式
    r3 = suggest_questions(context="", mode="feedback", project_id=None, top_k=3)
    _print_result("场景3: feedback 模式（改进建议）", r3)

    # 场景 4：Chroma 兜底（无 snapshot 项目 + context 为空）
    # 用一个不存在的 project_id 触发 Chroma 兜底
    r4 = suggest_questions(context="", mode="question", project_id="nonexistent-project-xyz", top_k=3)
    _print_result("场景4: Chroma 兜底（project_id 不存在，无 snapshot）", r4)

    # 验证
    print("\n" + "=" * 60)
    success_count = sum(1 for r in [r1, r2, r3, r4] if r.get("questions"))
    print(f"成功场景: {success_count}/4")

    # 单独验证 Chroma 兜底是否真正触发
    if r4.get("based_on", "").startswith("chroma"):
        print("✅ 场景4 正确走了 Chroma 兜底路径")
    elif r4.get("based_on") == "context_only":
        print("⚠️ 场景4 走了最终兜底（context_only）—— Chroma 向量库可能为空")
    elif r4.get("based_on", "").startswith("snapshot"):
        print("⚠️ 场景4 走了 snapshot 路径——project_id 不该有 snapshot，检查测试逻辑")

    if success_count == 4:
        print("✅ 全部场景返回了非空 questions，后端工作正常")
        return 0
    else:
        print("⚠️ 部分场景返回空，检查 LLM 配置或网络")
        return 1


if __name__ == "__main__":
    sys.exit(main())
