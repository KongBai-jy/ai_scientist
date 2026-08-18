"""
本地 PDF 文献入库 CLI
====================================

将本地 PDF 文件解析并塞入 Chroma 向量库，让 Explorer 能基于该文献做研究。
默认按 source 字段去重（已塞过同 source 的 PDF 会跳过整个文件）。

用法:
    # 默认去重 + chunk_size=1200
    python scripts/seed_pdf.py "docs/125道科学问题.pdf"

    # 指定 source 标识（推荐，便于 Explorer 引用时识别）
    python scripts/seed_pdf.py "docs/125道科学问题.pdf" --source "125 Questions (Science, 2025)"

    # 跳过去重（强制重新塞入，会重复）
    python scripts/seed_pdf.py "docs/xxx.pdf" --no-dedupe

    # 自定义切分大小
    python scripts/seed_pdf.py "docs/xxx.pdf" --chunk-size 1500 --overlap 150

后续可:
    python scripts/seed_chroma.py list                     # 核对总数
    python scripts/seed_chroma.py search "查询关键词"        # 测试检索
"""

import sys
import os
import argparse
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from services.paper_search_service import PaperSearchService  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="本地 PDF 文件解析并塞入 Chroma 向量库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", type=str, help="PDF 文件路径（相对路径会基于 cwd 解析）")
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="来源标识（默认用文件名）；用于去重和 Explorer 引用",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="跳过去重（强制重新塞入，会重复）",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1200,
        help="切分大小（默认 1200 字符）",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=100,
        help="切分重叠（默认 100）",
    )
    args = parser.parse_args()

    # 解析文件路径（相对路径基于 cwd）
    file_path = args.file
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(os.path.join(os.getcwd(), file_path))
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)

    source_label = args.source or os.path.basename(file_path)

    print("=" * 60)
    print(f"  本地 PDF 入库")
    print(f"  文件:      {file_path}")
    print(f"  来源标识:  {source_label}")
    print(f"  切分参数:  chunk_size={args.chunk_size}, overlap={args.overlap}")
    print(f"  去重:      {'否（强制重塞）' if args.no_dedupe else '是（按 source 跳过）'}")
    print("=" * 60)

    svc = PaperSearchService()
    result = svc.ingest_local_pdf(
        file_path=file_path,
        source=args.source,
        dedupe=not args.no_dedupe,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )

    print()
    if result.get("skipped"):
        print(f"⚠️ 已跳过（source={result['source']!r} 已存在）")
    elif result.get("error"):
        print(f"❌ 失败: {result['error']}")
        sys.exit(1)
    else:
        avg = result["total_chars"] // result["ingested"] if result["ingested"] else 0
        print(f"✅ 入库成功!")
        print(f"   总字符:     {result['total_chars']}")
        print(f"   入库 chunks: {result['ingested']}")
        print(f"   平均每 chunk: {avg} 字符")

    print()
    print("后续可:")
    print(f"  python scripts/seed_chroma.py list                    # 核对总数")
    print(f"  python scripts/seed_chroma.py search \"查询关键词\"      # 测试检索")


if __name__ == "__main__":
    main()
