"""手动塞入文献用于保留场景测试（不清理，作为基线数据）

目的：让向量库有非空的 arxiv_id 集合，再跑 test_cleanup_logic.py
       验证"手动塞的不会被误删，本次新增的会被清理"

用法:
    # 塞入两个主题的文献（默认每个主题 5 篇）
    python scripts/seed_test_papers.py "neural network" "deep learning"

    # 单个查询
    python scripts/seed_test_papers.py "causal inference"

    # 指定每个查询返回的文献数
    python scripts/seed_test_papers.py "transformer" -k 3

塞入后建议:
    python test_cleanup_logic.py "quantum computing"
    # 预期: 清理后 arxiv_id 数量 = 本脚本塞入后的数量（手动塞的全部保留）
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def main():
    parser = argparse.ArgumentParser(description="手动塞入文献用于保留场景测试")
    parser.add_argument("queries", nargs="+", help="要塞入的查询关键词列表")
    parser.add_argument("-k", "--max-results", type=int, default=5, help="每个查询塞入的文献数（默认 5）")
    args = parser.parse_args()

    print("=" * 60)
    print(f"手动塞入文献测试（{len(args.queries)} 个查询，每个最多 {args.max_results} 篇）")
    print("=" * 60)

    from services.paper_search_service import PaperSearchService
    svc = PaperSearchService()

    # ===== 1. 塞入前快照 =====
    print("\n[1/4] 塞入前快照...")
    pre_ids = svc.get_existing_arxiv_ids()
    print(f"    现有 {len(pre_ids)} 个 arxiv_id")
    if pre_ids:
        print(f"    前 5: {sorted(pre_ids)[:5]}")

    # ===== 2. 逐个查询塞入 =====
    print(f"\n[2/4] 按查询塞入文献（不清理，作为基线）...")
    total_ingested = 0
    for q in args.queries:
        result = svc.search_and_ingest(q, max_results=args.max_results)
        retrieved = result.get("retrieved", 0)
        ingested = result.get("ingested", 0)
        print(f"    '{q}': 检索 {retrieved} 篇, 入库 {ingested} 篇")
        total_ingested += ingested
    print(f"    本次共入库 {total_ingested} 篇")

    # ===== 3. 塞入后状态 =====
    print(f"\n[3/4] 塞入后状态...")
    post_ids = svc.get_existing_arxiv_ids()
    new_ids = post_ids - pre_ids
    print(f"    现有 {len(post_ids)} 个 arxiv_id（本次新增 {len(new_ids)} 个）")
    print(f"\n    全部 arxiv_id 列表（{len(post_ids)} 个）:")
    for i, aid in enumerate(sorted(post_ids), 1):
        marker = " [新增]" if aid in new_ids else " [保留]"
        print(f"      {i:2d}. {aid}{marker}")

    # ===== 4. 下一步提示 =====
    print(f"\n[4/4] 完成。下一步验证保留逻辑：")
    print(f"    python test_cleanup_logic.py \"<另一个不相关的查询>\"")
    print(f"    预期结果: 清理后回到 {len(post_ids)} 个 arxiv_id（手动塞的全部保留）")
    print("=" * 60)


if __name__ == "__main__":
    main()
