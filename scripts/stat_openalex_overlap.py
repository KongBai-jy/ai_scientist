"""
统计 papers/<topic>/openalex/*.json 中的文献与 Chroma 向量库的重叠情况。
纯 dry-run，不写入任何数据。

用法:
    venv\Scripts\python.exe scripts\stat_openalex_overlap.py
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

# 让 src/ 下的模块可导入
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from services.chroma_service import ChromaService


def load_openalex_papers(papers_dir: Path):
    """扫描所有 openalex JSON，返回 (paper_dict, total_count)"""
    papers = []
    for jf in sorted(papers_dir.glob("*/openalex/*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] 读取失败 {jf.name}: {e}")
            continue
        topic = jf.parent.parent.name
        for p in data.get("papers", []):
            doi = (p.get("doi") or "").strip()
            # 统一 doi 格式：去掉 https://doi.org/ 前缀
            doi_key = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
            papers.append({
                "title": p.get("title", ""),
                "doi": doi_key,
                "openalex_id": p.get("openalex_id", ""),
                "year": p.get("year"),
                "topic": topic,
                "file": jf.name,
            })
    return papers


def fetch_existing_ids(chroma: ChromaService):
    """从向量库拉取已有 arxiv_id + doi 集合"""
    store = chroma.load_or_create()
    result = {"arxiv": set(), "doi": set()}
    try:
        data = store._collection.get(include=["metadatas"])
        for meta in data.get("metadatas", []):
            if not isinstance(meta, dict):
                continue
            aid = meta.get("arxiv_id", "")
            if aid:
                result["arxiv"].add(aid)
            d = meta.get("doi", "")
            if d:
                # 统一格式
                d_clean = d.replace("https://doi.org/", "").replace("http://doi.org/", "")
                result["doi"].add(d_clean)
    except Exception as e:
        print(f"[ERROR] 读取向量库 metadata 失败: {e}")
    return result


def main():
    papers_dir = ROOT / "papers"
    print(f"扫描目录: {papers_dir}")
    print("=" * 60)

    # 1. 加载 OpenAlex 文献
    all_papers = load_openalex_papers(papers_dir)
    print(f"OpenAlex JSON 总文献数: {len(all_papers)}")

    # 2. 连接向量库
    chroma = ChromaService()
    existing = fetch_existing_ids(chroma)
    print(f"向量库已有: arxiv_id={len(existing['arxiv'])}, doi={len(existing['doi'])}")
    print("=" * 60)

    # 3. 逐条比对
    matched_by_doi = []
    unmatched = []
    no_doi = []

    for p in all_papers:
        if not p["doi"]:
            no_doi.append(p)
            continue
        if p["doi"] in existing["doi"]:
            matched_by_doi.append(p)
        else:
            unmatched.append(p)

    # 4. 按主题统计
    topic_stats = defaultdict(lambda: {"total": 0, "matched": 0, "new": 0, "no_doi": 0})
    for p in all_papers:
        t = p["topic"]
        topic_stats[t]["total"] += 1
        if not p["doi"]:
            topic_stats[t]["no_doi"] += 1
        elif p["doi"] in existing["doi"]:
            topic_stats[t]["matched"] += 1
        else:
            topic_stats[t]["new"] += 1

    # 5. 输出汇总
    print(f"\n{'='*60}")
    print(f"  总文献: {len(all_papers)}")
    print(f"  DOI 匹配(已入库): {len(matched_by_doi)} ({len(matched_by_doi)/max(len(all_papers),1)*100:.1f}%)")
    print(f"  DOI 未匹配(新增): {len(unmatched)} ({len(unmatched)/max(len(all_papers),1)*100:.1f}%)")
    print(f"  无 DOI(无法去重): {len(no_doi)} ({len(no_doi)/max(len(all_papers),1)*100:.1f}%)")
    print(f"{'='*60}")

    # 6. 按主题明细
    print(f"\n{'主题':<25} {'总数':>5} {'已入库':>6} {'新增':>5} {'无DOI':>5} {'新增率':>7}")
    print("-" * 60)
    for topic in sorted(topic_stats.keys()):
        s = topic_stats[topic]
        rate = f"{s['new']/max(s['total'],1)*100:.0f}%"
        print(f"{topic:<25} {s['total']:>5} {s['matched']:>6} {s['new']:>5} {s['no_doi']:>5} {rate:>7}")

    # 7. 列出前 20 条新增文献（供人工抽查）
    if unmatched:
        print(f"\n新增文献示例 (前 20 条):")
        print("-" * 60)
        for p in unmatched[:20]:
            print(f"  [{p['topic']}] {p['title'][:60]}  DOI={p['doi']}")

    # 8. 结论
    new_rate = len(unmatched) / max(len(all_papers), 1) * 100
    print(f"\n{'='*60}")
    if new_rate < 20:
        print(f"结论: 新增率仅 {new_rate:.0f}%，大部分文献已在库中，全量入库收益不大。")
    elif new_rate < 50:
        print(f"结论: 新增率 {new_rate:.0f}%，有一定补充价值，建议选择性入库。")
    else:
        print(f"结论: 新增率 {new_rate:.0f}%，大量文献未入库，建议全量入库。")
    print(f"注意: {len(no_doi)} 篇无 DOI 的文献无法通过 DOI 去重，可能产生重复。")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
