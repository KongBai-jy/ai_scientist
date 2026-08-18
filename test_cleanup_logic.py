"""策略 B 清理逻辑单元测试（不跑 LLM pipeline，秒级完成）

验证三个步骤的 set diff 清理逻辑：
    1. 快照现有 arxiv_id（pipeline 开始前）
    2. 检索 + 入库新文献
    3. 算 diff，精确清理本次塞入的

用法:
    python test_cleanup_logic.py "causal inference"
    python test_cleanup_logic.py "YBCO superconductor"
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "causal inference"
    print("=" * 60)
    print(f"策略 B 清理逻辑测试（query={query!r})")
    print("=" * 60)

    # 懒加载 ChromaService，失败立即抛错
    print("\n[1/7] 初始化 PaperSearchService...")
    from services.paper_search_service import PaperSearchService
    svc = PaperSearchService()
    print("    OK")

    print("\n[2/7] 快照当前向量库 arxiv_id 集合（模拟 pipeline 开始前）...")
    pre_ids = svc.get_existing_arxiv_ids()
    print(f"    现有 {len(pre_ids)} 个 arxiv_id")
    if pre_ids:
        print(f"    前 5: {sorted(pre_ids)[:5]}")

    print(f"\n[3/7] 检索并塞入 '{query}' 相关文献（max_results=5）...")
    result = svc.search_and_ingest(query, max_results=5)
    print(f"    检索 {result.get('retrieved', 0)} 篇, 入库 {result.get('ingested', 0)} 篇")

    print("\n[4/7] 快照塞入后的 arxiv_id 集合（模拟 pipeline 结束后）...")
    post_ids = svc.get_existing_arxiv_ids()
    print(f"    现有 {len(post_ids)} 个 arxiv_id")

    new_ids = post_ids - pre_ids
    print("\n[5/7] Diff 计算（策略 B 核心逻辑）")
    print(f"    本次新增 {len(new_ids)} 个: {sorted(new_ids)}")
    preserved = post_ids - new_ids
    print(f"    用户手动塞的保留 {len(preserved)} 个: {sorted(preserved)[:5]}{'...' if len(preserved) > 5 else ''}")

    print(f"\n[6/7] 调用 cleanup_by_arxiv_ids 精确清理本次新增...")
    cleaned = svc.cleanup_by_arxiv_ids(list(new_ids))
    print(f"    实际清理 {cleaned} 篇")

    print("\n[7/7] 验证：清理后的 arxiv_id 集合")
    final_ids = svc.get_existing_arxiv_ids()
    print(f"    现有 {len(final_ids)} 个 arxiv_id")
    if final_ids:
        print(f"    前 5: {sorted(final_ids)[:5]}")

    print("\n" + "=" * 60)
    if final_ids == pre_ids:
        print(f"✅ 测试通过！清理后状态 == 清理前状态（{len(pre_ids)} → {len(final_ids)}）")
        print("   策略 B 工作正常：临时文献已清理，手动塞的保留")
    else:
        diff = final_ids.symmetric_difference(pre_ids)
        print(f"⚠️ 清理不完整！")
        print(f"   清理前: {len(pre_ids)} 个, 清理后: {len(final_ids)} 个")
        print(f"   差异: {sorted(diff)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
