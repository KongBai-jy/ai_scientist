"""
本地 PDF 文献入库 CLI
====================

将本地 PDF 文件解析并塞入 Chroma 向量库，让 Explorer 能基于该文献做研究。
默认按 source 字段去重（已塞过同 source 的 PDF 会跳过整个文件）。

用法:
    # 单文件入库
    python scripts/seed_pdf.py single "papers/physics/ybco_2024.pdf" --source "physics_ybco_2024"

    # 批量入库（扫描 papers/ 下所有 PDF，递归子目录）
    python scripts/seed_pdf.py batch
    python scripts/seed_pdf.py batch "papers"
    python scripts/seed_pdf.py batch --dry-run

    # 通用参数（两种模式都支持）
    python scripts/seed_pdf.py single "xxx.pdf" --no-dedupe
    python scripts/seed_pdf.py batch --chunk-size 1500 --overlap 150

后续可:
    python scripts/seed_chroma.py list                     # 核对总数
    python scripts/seed_chroma.py search "查询关键词"        # 测试检索
    python scripts/seed_chroma.py clean --source "xxx"      # 清理指定来源
"""

import sys
import os
import argparse
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from services.paper_search_service import PaperSearchService  # noqa: E402


def _resolve_path(p: str) -> str:
    """将相对路径解析为绝对路径"""
    if not os.path.isabs(p):
        return os.path.abspath(os.path.join(os.getcwd(), p))
    return p


def _build_ingest_args(args) -> dict:
    """从通用参数构建 ingest_local_pdf 的 kwargs"""
    return dict(
        dedupe=not args.no_dedupe,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )


def cmd_single(args):
    """单文件入库"""
    file_path = _resolve_path(args.file)
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)

    source_label = args.source or os.path.basename(file_path)

    print("=" * 60)
    print(f"  单文件 PDF 入库")
    print(f"  文件:      {file_path}")
    print(f"  来源标识:  {source_label}")
    print(f"  切分参数:  chunk_size={args.chunk_size}, overlap={args.overlap}")
    print(f"  去重:      {'否（强制重塞）' if args.no_dedupe else '是（按 source 跳过）'}")
    print("=" * 60)

    svc = PaperSearchService()
    result = svc.ingest_local_pdf(
        file_path=file_path,
        source=args.source,
        **_build_ingest_args(args),
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


def _discover_pdfs(folder: str) -> list:
    """递归扫描文件夹内所有 PDF"""
    folder_path = Path(folder)
    if not folder_path.exists():
        return []
    pdfs = sorted(folder_path.rglob("*.pdf"))
    return [p for p in pdfs if not p.name.startswith("~$")]


def cmd_batch(args):
    """批量入库"""
    folder_path = _resolve_path(args.folder)

    print("=" * 60)
    print(f"  批量 PDF 入库")
    print(f"  扫描文件夹: {folder_path}")
    print(f"  去重:      {'否' if args.no_dedupe else '是（按 source 跳过）'}")
    print(f"  切分参数:  chunk_size={args.chunk_size}, overlap={args.overlap}")
    print("=" * 60)

    pdfs = _discover_pdfs(folder_path)
    print(f"\n📂 发现 {len(pdfs)} 个 PDF 文件:\n")
    for i, p in enumerate(pdfs, 1):
        rel = os.path.relpath(p, folder_path)
        size_mb = p.stat().st_size / 1024 / 1024
        print(f"  {i:3d}. {rel}  ({size_mb:.1f} MB)")

    if args.dry_run:
        print("\n🔍 Dry-run 模式，不执行入库。")
        return

    if not pdfs:
        print("\n⚠️  没有找到 PDF 文件。请把论文放进 papers/ 文件夹。")
        return

    svc = PaperSearchService()
    success = 0
    skipped = 0
    failed = 0
    total_chunks = 0

    for pdf_path in pdfs:
        rel = os.path.relpath(pdf_path, folder_path)
        source_label = rel.replace("\\", "/")  # 用相对路径做 source，保证唯一

        print(f"\n{'─' * 60}")
        print(f"📄 [{success + skipped + failed + 1}/{len(pdfs)}] {rel}")
        print(f"   source: {source_label}")

        try:
            result = svc.ingest_local_pdf(
                file_path=str(pdf_path),
                source=source_label,
                **_build_ingest_args(args),
            )

            if result.get("skipped"):
                print(f"   ⚠️  跳过（source 已存在）")
                skipped += 1
            elif result.get("error"):
                print(f"   ❌ 失败: {result['error']}")
                failed += 1
            else:
                chunks = result.get("ingested", 0)
                total_chunks += chunks
                chars = result.get("total_chars", 0)
                avg = chars // chunks if chunks else 0
                print(f"   ✅ 成功: {chunks} chunks, {chars} 字符 (avg {avg}/chunk)")
                success += 1
        except Exception as e:
            print(f"   ❌ 异常: {e}")
            failed += 1

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  入库完成")
    print(f"  成功:  {success}")
    print(f"  跳过:  {skipped}（已存在）")
    print(f"  失败:  {failed}")
    print(f"  总计:  {total_chunks} chunks")
    print(f"{'=' * 60}")

    print("\n后续可:")
    print(f"  python scripts/seed_chroma.py list                    # 核对总数")
    print(f"  python scripts/seed_chroma.py search \"关键词\"        # 测试检索")


def _add_common_args(parser):
    """两种子命令共用的参数"""
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


def main():
    parser = argparse.ArgumentParser(
        description="本地 PDF 文件解析并塞入 Chroma 向量库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # single 子命令
    p_single = sub.add_parser("single", help="单文件入库")
    p_single.add_argument("file", type=str, help="PDF 文件路径")
    p_single.add_argument(
        "--source",
        type=str,
        default=None,
        help="来源标识（默认用文件名）；用于去重和 Explorer 引用",
    )
    _add_common_args(p_single)

    # batch 子命令
    p_batch = sub.add_parser("batch", help="批量入库（扫描文件夹）")
    p_batch.add_argument(
        "folder",
        nargs="?",
        default="papers",
        help="PDF 文件夹路径（默认: papers）",
    )
    p_batch.add_argument(
        "--dry-run",
        action="store_true",
        help="仅列出待入库文件，不实际执行",
    )
    _add_common_args(p_batch)

    args = parser.parse_args()

    if args.command == "single":
        cmd_single(args)
    elif args.command == "batch":
        cmd_batch(args)


if __name__ == "__main__":
    main()
