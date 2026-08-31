"""
将 papers/<topic>/arxiv/ 下的新 PDF 文献按 science125_papers_final.json 的元数据
去重后批量塞入 Chroma knowledge_base 集合。

用法:
    python scripts/ingest_new_papers.py           # dry-run，仅打印计划
    python scripts/ingest_new_papers.py --apply   # 实际入库
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import os
import re
import json
import glob
import argparse
from typing import Dict, List, Optional, Tuple

# 加载 .env 以获取 Embedding API Key
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "papers", "science125_papers_final.json")
PAPERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "papers")

# 中文分类 -> 英文文件夹名
CAT_MAP = {
    "人工智能": "artificial_intelligence",
    "信息科学": "information_science",
    "化学": "chemistry",
    "医学与健康": "medicine_health",
    "天文学": "astronomy",
    "工程与材料科学": "engineering_materials",
    "数学科学": "mathematical_sciences",
    "物理学": "physics",
    "生态学": "ecology",
    "生物学": "biology",
    "神经科学": "neuroscience",
    "能源科学": "energy_science",
}


def base_arxiv_id(aid: str) -> str:
    m = re.match(r"^(\d{4}\.\d{4,5})", aid)
    return m.group(1) if m else aid


def load_manifest() -> Dict[str, dict]:
    """返回 arxiv_base_id -> paper meta 的映射。"""
    with open(MANIFEST, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for prob in data.get("data", []):
        cat = prob.get("category", "")
        topic = CAT_MAP.get(cat, cat)
        for p in prob.get("papers", []):
            aid = p.get("arxiv_id", "")
            if not aid:
                continue
            b = base_arxiv_id(aid)
            if b not in result:  # 首次出现优先
                result[b] = {
                    "title": p.get("title", ""),
                    "authors": p.get("authors", []),
                    "year": p.get("year", ""),
                    "abstract": p.get("abstract", ""),
                    "source": p.get("source", "arxiv"),
                    "doi": p.get("doi", ""),
                    "url": p.get("url", ""),
                    "topic": topic,
                    "problem_cn": prob.get("problem_cn", ""),
                    "problem_en": prob.get("problem_en", ""),
                }
    return result


def get_existing_arxiv_ids() -> set:
    import chromadb
    client = chromadb.PersistentClient(path="./data/chroma_db")
    col = client.get_collection("knowledge_base")
    res = col.get(include=["metadatas"])
    ids = set()
    for m in res["metadatas"]:
        if not m:
            continue
        aid = m.get("arxiv_id", "")
        if aid:
            ids.add(base_arxiv_id(aid))
    return ids


def scan_new_pdfs(existing: set) -> List[Tuple[str, str]]:
    """返回 [(base_id, pdf_path), ...] 列表（已去重）。"""
    seen = set()
    result = []
    for pdf in sorted(glob.glob(os.path.join(PAPERS_DIR, "*", "arxiv", "*.pdf"))):
        stem = os.path.basename(pdf).replace(".pdf", "")
        b = base_arxiv_id(stem)
        if b in existing or b in seen:
            continue
        seen.add(b)
        result.append((b, pdf))
    return result


def ingest_abstract_only(meta: dict, base_id: str, apply: bool) -> int:
    """无 PDF 文本时，用 abstract 作为单条文档入库。"""
    import chromadb
    from langchain_community.embeddings import DashScopeEmbeddings

    emb = DashScopeEmbeddings(
        model=os.getenv("QWEN_MODEL_EMBEDDING", "qwen3.7-text-embedding"),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY_EMBEDDING") or os.getenv("DASHSCOPE_API_KEY"),
    )

    text = meta.get("abstract", meta.get("title", ""))
    if not text.strip():
        print(f"  ⚠️  {base_id} 无 abstract 且无 title，跳过")
        return 0

    metadata = {
        "arxiv_id": base_id,
        "title": meta.get("title", ""),
        "source": meta.get("source", "arxiv"),
        "year": str(meta.get("year", "")),
        "field": meta.get("topic", ""),
        "doi": meta.get("doi", ""),
        "url": meta.get("url", ""),
        "ingest_mode": "abstract",
        "chunk_index": 0,
        "total_chunks": 1,
    }

    if apply:
        client = chromadb.PersistentClient(path="./data/chroma_db")
        col = client.get_collection("knowledge_base")
        embeddings = [emb.embed_query(text)]
        col.add(
            documents=[text],
            metadatas=[metadata],
            embeddings=embeddings,
            ids=[f"arxiv_{base_id}_abs"],
        )
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际入库（默认仅 dry-run）")
    args = ap.parse_args()

    manifest = load_manifest()
    existing = get_existing_arxiv_ids()
    new_pdfs = scan_new_pdfs(existing)

    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] 待入库新 PDF: {len(new_pdfs)}")
    print(f"Chroma 已有 arXiv ID: {len(existing)}")
    print()

    # 统计按 topic 分布
    from collections import Counter
    dist = Counter()
    missing_meta = []
    for base_id, pdf_path in new_pdfs:
        meta = manifest.get(base_id)
        if meta:
            dist[meta.get("topic", "unknown")] += 1
        else:
            missing_meta.append(base_id)
            dist["(无元数据)"] += 1

    print("按 topic 分布:")
    for k, v in sorted(dist.items()):
        print(f"  {k}: {v}")
    print()

    if missing_meta:
        print(f"⚠️  {len(missing_meta)} 个 PDF 在 manifest 中找不到元数据（仍会入库但缺字段）:")
        for x in missing_meta[:10]:
            print(f"    {x}")
        if len(missing_meta) > 10:
            print(f"    ... 还有 {len(missing_meta)-10} 个")
        print()

    if not args.apply:
        print(f"预览前 10 条:")
        for base_id, pdf_path in new_pdfs[:10]:
            meta = manifest.get(base_id, {})
            title = meta.get("title", "(无标题)")[:60]
            print(f"  {base_id} | {title} | topic={meta.get('topic','?')}")
        print(f"\n确认无误后运行: python scripts/ingest_new_papers.py --apply")
        return

    # 执行入库
    total = 0
    skipped = 0
    errors = []
    for i, (base_id, pdf_path) in enumerate(new_pdfs, 1):
        meta = manifest.get(base_id, {})
        try:
            n = ingest_abstract_only(meta, base_id, apply=True)
            total += n
            if i % 20 == 0:
                print(f"  [{i}/{len(new_pdfs)}] 已入库 {total} 条...")
        except Exception as e:
            skipped += 1
            errors.append((base_id, str(e)))

    print(f"\n✅ 完成：成功入库 {total} 条 abstract 文档，跳过 {skipped} 个")
    if errors:
        print(f"\n错误详情:")
        for bid, err in errors[:5]:
            print(f"  {bid}: {err[:120]}")


if __name__ == "__main__":
    main()
