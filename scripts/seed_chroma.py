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


# ============================================================
# 默认测试数据（与项目主题"科学假设生成"相关的多学科文献片段）
# ============================================================

DEFAULT_SEED_DATA = [
    # === 高温超导领域 ===
    {
        "content": (
            "YBCO（钇钡铜氧）超导体的临界转变温度 Tc 约 92 K，"
            "是首个被发现的液氮温区超导材料。其晶体结构为钙钛矿型畸变结构，"
            "CuO2 面是承载超导电流的关键单元。研究表明，通过氧含量调节可显著影响 Tc，"
            "最佳氧含量对应 YBa2Cu3O7-δ 中 δ≈0.05。"
        ),
        "source": "Bednorz & Müller, 1986, Z. Phys. B",
        "year": "1986",
        "field": "高温超导",
        "doi": "10.1007/BF01303701",
    },
    {
        "content": (
            "在 YBCO 中引入人工拓扑缺陷（如螺位错、晶界）可作为磁通钉扎中心，"
            "提升临界电流密度 Jc。实验显示，5% 摩尔分数的 BaZrO3 掺杂可使 Jc 提升 3-5 倍，"
            "但对 Tc 的影响在 2 K 以内。"
        ),
        "source": "MacManus-Driscoll et al., 2004, Nat. Mater.",
        "year": "2004",
        "field": "高温超导",
        "doi": "10.1038/nmat1196",
    },
    {
        "content": (
            "高压下 YBCO 的 Tc 可提升至 105 K 左右，压力诱导的晶格压缩改变了 CuO2 面的键长，"
            "进而优化载流子浓度。然而当压力超过 15 GPa 时，Tc 反而下降，"
            "这与过度压缩导致的电子结构畸变有关。"
        ),
        "source": "Almasan et al., 1992, Phys. Rev. B",
        "year": "1992",
        "field": "高温超导",
    },
    # === 统计物理 / 相变 ===
    {
        "content": (
            "伊辛模型展示了局部自旋相互作用如何涌现出宏观磁化相变。"
            "二维伊辛模型在临界温度 Tc 处出现精确的相变点，"
            "其磁化强度 M ~ (Tc - T)^β，临界指数 β=1/8。"
            "该模型为理解高温超导中的微观-宏观映射提供了经典范式。"
        ),
        "source": "Onsager, 1944, Phys. Rev.",
        "year": "1944",
        "field": "统计物理",
    },
    {
        "content": (
            "重整化群理论揭示了相变临界点附近的尺度不变性，"
            "关联长度 ξ 在 Tc 处发散。Wilson 的理论框架统一了各类相变现象的普适类，"
            "为跨学科类比提供了理论基础。"
        ),
        "source": "Wilson, 1971, Phys. Rev. B",
        "year": "1971",
        "field": "统计物理",
    },
    # === 复杂网络 / 神经科学 ===
    {
        "content": (
            "大脑神经网络的临界性假说认为，皮层神经元集群运行在相变临界点附近，"
            "以最大化信息处理能力与适应性。实验证据来自神经元雪崩的幂律分布，"
            "其指数约为 -3/2，与临界分支过程的预测一致。"
        ),
        "source": "Beggs & Plenz, 2003, J. Neurosci.",
        "year": "2003",
        "field": "神经科学",
    },
    {
        "content": (
            "小世界网络模型（Watts-Strogatz）揭示了少量长程连接即可大幅降低网络平均路径长度，"
            "同时保持高聚类系数。该模型可解释大脑神经网络的局部专业化与全局整合并存现象，"
            "也为超导材料中的电子关联拓扑提供了类比。"
        ),
        "source": "Watts & Strogatz, 1998, Nature",
        "year": "1998",
        "field": "复杂网络",
    },
    # === 因果推断 / 科学方法论 ===
    {
        "content": (
            "Pearl 的 do-calculus 提供了从观察数据中识别因果效应的形式化框架。"
            "其核心是后门准则与前门准则，能在存在混杂变量的情况下识别真实因果效应。"
            "该理论为科学假设的可证伪化检验提供了数学基础。"
        ),
        "source": "Pearl, 1995, Biometrika",
        "year": "1995",
        "field": "因果推断",
    },
    {
        "content": (
            "反事实推理是科学假设检验的核心方法。给定观测数据 X 和结果 Y，"
            "通过构建结构因果模型可计算 P(Y | do(X=x)) 与 P(Y | do(X=x')) 的差异，"
            "从而量化 X 对 Y 的因果效应。这是可证伪性原则的数学实现。"
        ),
        "source": "Pearl, 2009, Causality (Cambridge)",
        "year": "2009",
        "field": "因果推断",
    },
]


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