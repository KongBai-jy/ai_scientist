"""
在线文献检索 CLI（基于 arXiv API）
====================================

用法:
    # 仅检索（不入库，dry-run）
    python scripts/search_papers.py search "causal inference" -k 5

    # 检索 + 自动塞入 Chroma（带去重）
    python scripts/search_papers.py ingest "causal inference" -k 5
    python scripts/search_papers.py ingest "quantum entanglement" --no-dedupe

    # 查看当前向量库中所有论文（复用 seed_chroma 的 list 能力）
    python scripts/seed_chroma.py list

注意:
    - 英文关键词检索效果最佳（arXiv 为英文数据库）
    - 中文问题建议先翻译成英文关键词，或直接传整句英文
"""

import sys
import argparse
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from services.paper_search_service import PaperSearchService  # noqa: E402


def cmd_search(args):
    """仅检索，打印结果（不入库）"""
    svc = PaperSearchService()
    papers = svc.search(query=args.query, max_results=args.top_k)

    if not papers:
        print("未检索到任何论文")
        return

    print(f"检索完成: 共 {len(papers)} 篇\n" + "=" * 70)
    for i, p in enumerate(papers, 1):
        print(f"[{i}] {p.title}")
        print(f"    年份: {p.year or '?'}    来源: {p.source}")
        if p.authors:
            print(f"    作者: {', '.join(p.authors[:3])}{'...' if len(p.authors) > 3 else ''}")
        if p.doi:
            print(f"    DOI:  {p.doi}")
        if p.arxiv_id:
            print(f"    arXiv: {p.arxiv_id}")
        if p.url:
            print(f"    URL:  {p.url}")
        preview = p.abstract[:150].replace("\n", " ") + ("..." if len(p.abstract) > 150 else "")
        print(f"    摘要: {preview}")
        print()


def cmd_ingest(args):
    """检索 + 塞入 Chroma"""
    svc = PaperSearchService()
    result = svc.search_and_ingest(
        query=args.query,
        max_results=args.top_k,
        dedupe=not args.no_dedupe,
    )

    print("=" * 70)
    print(f"  检索关键词: {result['query']}")
    print(f"  检索到:    {result['retrieved']} 篇")
    print(f"  已写入:    {result['ingested']} 篇")
    print(f"  跳过(去重): {result['skipped']} 篇")
    print("=" * 70)

    if result["ingested"] > 0:
        print("\n已入库论文:")
        for p in result["papers"]:
            print(f"  - {p['title']}  ({p['year'] or '?'})  [{p['source']}]")
        print(f"\n后续可:")
        print(f"  python scripts/seed_chroma.py list                          # 核对总数")
        print(f"  python scripts/seed_chroma.py search \"{result['query']}\"   # 测试相似度")


def main():
    parser = argparse.ArgumentParser(
        description="在线文献检索与入库（基于 arXiv API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search 子命令
    p_search = sub.add_parser("search", help="仅检索（不入库）")
    p_search.add_argument("query", type=str, help="搜索关键词（建议英文）")
    p_search.add_argument("-k", "--top-k", type=int, default=5, help="返回数（默认 5）")

    # ingest 子命令
    p_ingest = sub.add_parser("ingest", help="检索并写入 Chroma")
    p_ingest.add_argument("query", type=str, help="搜索关键词（建议英文）")
    p_ingest.add_argument("-k", "--top-k", type=int, default=5, help="返回数（默认 5）")
    p_ingest.add_argument(
        "--no-dedupe",
        action="store_true",
        help="跳过去重检查（默认会基于 arxiv_id/doi 去重）",
    )

    args = parser.parse_args()

    if args.command == "search":
        cmd_search(args)
    elif args.command == "ingest":
        cmd_ingest(args)


if __name__ == "__main__":
    main()
