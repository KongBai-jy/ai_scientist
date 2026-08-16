"""
Chroma 向量库测试数据填充脚本
================================

用法:
    # 塞入默认测试数据（高温超导 + 神经科学 + 复杂网络等领域）
    python scripts/seed_chroma.py seed

    # 查看当前向量库中的所有文档
    python scripts/seed_chroma.py list

    # 测试检索（验证塞进去的数据能不能被搜到）
    python scripts/seed_chroma.py search "如何提升 YBCO 超导转变温度"

    # 清空集合（注意：会删除所有数据，不可恢复）
    python scripts/seed_chroma.py clear

自定义数据:
    编辑本文件底部的 DEFAULT_SEED_DATA 列表，添加你自己的文献片段即可。
    每条数据需要 3 个字段:
      - content:  文献正文片段（必填，不能为空字符串）
      - source:   论文/资料来源（必填，至少 3 个字符，否则 Explorer 会拒绝）
      - year:     发表年份（可选）
      - field:    学科领域（可选，便于分类查询）
      - doi:      论文 DOI（可选）
"""

import sys
import json
import argparse
from pathlib import Path

# 把 src 加入 path，便于直接复用 ChromaService
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from services.chroma_service import ChromaService  # noqa: E402
from config.seed_data import DEFAULT_SEED_DATA  # noqa: E402


# ============================================================
# 默认测试数据（与项目主题"科学假设生成"相关的多学科文献片段）
# ============================================================



# ============================================================
# 命令实现
# ============================================================

def cmd_seed(args):
    """塞入测试数据"""
    service = ChromaService()

    texts = [d["content"] for d in DEFAULT_SEED_DATA]
    metadatas = [
        {k: v for k, v in d.items() if k != "content"}
        for d in DEFAULT_SEED_DATA
    ]

    # 过滤掉空 content（防止之前的 400 报错）
    valid = [(t, m) for t, m in zip(texts, metadatas) if t and t.strip()]
    if len(valid) != len(texts):
        print(f"⚠️  跳过 {len(texts) - len(valid)} 条空内容文档")

    print(f"准备塞入 {len(valid)} 条文档到 Chroma...")
    print(f"  persist_directory : {service.persist_directory}")
    print(f"  collection_name   : {service.collection_name}")
    print(f"  embedding_model   : {service.embeddings.model}")

    service.add_documents(
        texts=[t for t, _ in valid],
        metadatas=[m for _, m in valid],
    )

    print(f"\n✅ 成功塞入 {len(valid)} 条文档")
    print("\n调用建议：")
    print("  python scripts/seed_chroma.py list      # 查看塞入的数据")
    print("  python scripts/seed_chroma.py search \"YBCO\"   # 测试检索")


def cmd_list(args):
    """列出当前所有文档"""
    store = ChromaService().load_or_create()
    # chromadb 的底层 API
    collection = store._collection
    result = collection.get(include=["documents", "metadatas"])

    docs = result.get("documents", [])
    metas = result.get("metadatas", [])
    ids = result.get("ids", [])

    if not docs:
        print("向量库为空，请先运行: python scripts/seed_chroma.py seed")
        return

    print(f"共 {len(docs)} 条文档\n" + "=" * 60)
    for i, (doc_id, doc, meta) in enumerate(zip(ids, docs, metas), 1):
        preview = doc[:80].replace("\n", " ") + ("..." if len(doc) > 80 else "")
        print(f"[{i}] id={doc_id}")
        print(f"    source: {meta.get('source', '未知')}")
        print(f"    year  : {meta.get('year', '?')}  field: {meta.get('field', '?')}")
        print(f"    内容  : {preview}")
        print()


def cmd_search(args):
    """测试检索"""
    query = args.query
    k = args.top_k
    print(f"查询: {query!r}  (top_k={k})\n" + "=" * 60)

    results = ChromaService().similarity_search(query, k=k)
    if not results:
        print("未检索到任何文档，请先运行: python scripts/seed_chroma.py seed")
        return

    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        preview = r["content"][:100].replace("\n", " ")
        print(f"[{i}] score={r['score']:.4f}")
        print(f"    source: {meta.get('source', '未知')}")
        print(f"    内容  : {preview}...")
        print()


def cmd_clear(args):
    """清空集合"""
    service = ChromaService()
    if not args.yes:
        confirm = input(
            f"确认清空集合 '{service.collection_name}'? "
            f"所有数据将不可恢复 [y/N]: "
        ).strip().lower()
        if confirm != "y":
            print("已取消")
            return
    service.delete_collection()
    print(f"✅ 已清空集合 '{service.collection_name}'")


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Chroma 向量库测试数据管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="塞入默认测试数据")
    sub.add_parser("list", help="列出所有文档")

    p_search = sub.add_parser("search", help="测试检索")
    p_search.add_argument("query", type=str, help="查询文本")
    p_search.add_argument("-k", "--top-k", type=int, default=5, help="返回文档数")

    p_clear = sub.add_parser("clear", help="清空集合")
    p_clear.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    args = parser.parse_args()

    if args.command == "seed":
        cmd_seed(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "clear":
        cmd_clear(args)


if __name__ == "__main__":
    main()