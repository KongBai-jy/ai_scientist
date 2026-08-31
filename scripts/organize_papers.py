"""
将 papers/ 根目录下散落的 arXiv PDF 按 science125_papers_final.json 的元数据
（arxiv_id -> 中文分类）归类移动到 papers/<topic>/arxiv/ 下，并做去重。

用法:
    python scripts/organize_papers.py           # dry-run，仅打印计划
    python scripts/organize_papers.py --apply   # 实际移动
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import os
import re
import json
import glob
import shutil
import argparse
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS_DIR = os.path.join(ROOT, "papers")
MANIFEST = os.path.join(PAPERS_DIR, "science125_papers_final.json")

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

# 旧式 arXiv ID 前缀 -> 英文 topic（science125 manifest 覆盖不到的）
LEGACY_PREFIX_MAP = {
    "gr-qc": "physics",
    "hep-th": "physics",
    "hep-ph": "physics",
    "quant-ph": "physics",
    "astro-ph": "astronomy",
    "cond-mat": "physics",
    "math": "mathematical_sciences",
    "cs": "information_science",
    "nlin": "mathematical_sciences",
    "q-bio": "biology",
}

# 保留在根目录、不移动的源文档（被 Chroma file_path 直接引用）
KEEP_IN_ROOT = {"Science_2025_125_Questions.pdf", "science125_papers_final.json"}


def base_arxiv_id(stem: str) -> str:
    """从文件名 stem 提取 arxiv base id（去版本号）。"""
    m = re.match(r"^(\d{4}\.\d{4,5})", stem)
    if m:
        return m.group(1)
    return stem


def legacy_topic(stem: str):
    """旧式 ID（如 gr-qc9511027）按前缀映射 topic。"""
    for prefix, topic in LEGACY_PREFIX_MAP.items():
        if stem.startswith(prefix):
            return topic
    return None


def load_id_to_topic():
    with open(MANIFEST, "r", encoding="utf-8") as f:
        data = json.load(f)
    id_to_topics = {}
    for prob in data.get("data", []):
        cat = prob.get("category", "")
        topic = CAT_MAP.get(cat)
        if not topic:
            continue
        for p in prob.get("papers", []):
            aid = p.get("arxiv_id")
            if aid:
                id_to_topics.setdefault(base_arxiv_id(aid), set()).add(topic)
    return id_to_topics


def decide_topic(stem: str, id_to_topics) -> str:
    base = base_arxiv_id(stem)
    topics = id_to_topics.get(base)
    if topics:
        # 多 topic 时按字母序取第一个，保证确定性去重
        return sorted(topics)[0]
    legacy = legacy_topic(stem)
    if legacy:
        return legacy
    return "unsorted"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际移动文件（默认仅 dry-run）")
    args = ap.parse_args()

    id_to_topics = load_id_to_topic()
    loose = sorted(glob.glob(os.path.join(PAPERS_DIR, "*.pdf")))

    plan = []
    dist = Counter()
    for pdf in loose:
        fname = os.path.basename(pdf)
        if fname in KEEP_IN_ROOT:
            dist["(kept in root)"] += 1
            continue
        stem = fname[:-4]
        topic = decide_topic(stem, id_to_topics)
        dest_dir = os.path.join(PAPERS_DIR, topic, "arxiv")
        dest = os.path.join(dest_dir, fname)
        plan.append((pdf, dest, topic))
        dist[topic] += 1

    print(f"{'[APPLY]' if args.apply else '[DRY-RUN]'} 共 {len(plan)} 个 PDF 待归类\n")
    print("分类分布:")
    for k, v in sorted(dist.items()):
        print(f"  {k}: {v}")
    print()

    # 检测目标冲突（去重）
    seen = {}
    conflicts = []
    for src, dest, topic in plan:
        if dest in seen:
            conflicts.append((src, dest))
        seen[dest] = src
    if conflicts:
        print(f"⚠️  发现 {len(conflicts)} 个目标冲突（同名文件，去重保留一个）:")
        for src, dest in conflicts:
            print(f"    {os.path.basename(src)} -> {dest}")
        print()

    unsorted = [p for p in plan if p[2] == "unsorted"]
    if unsorted:
        print(f"⚠️  {len(unsorted)} 个无法匹配 topic（将放入 papers/unsorted/arxiv/）:")
        for src, _, _ in unsorted:
            print(f"    {os.path.basename(src)}")
        print()

    if not args.apply:
        print("预览前 20 条移动:")
        for src, dest, _ in plan[:20]:
            print(f"  {os.path.basename(src)} -> {os.path.relpath(dest, PAPERS_DIR)}")
        print("\n确认无误后运行: python scripts/organize_papers.py --apply")
        return

    # 执行移动
    moved = 0
    skipped_dup = 0
    for src, dest, topic in plan:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            # 目标已存在同名文件 -> 去重，删除源
            os.remove(src)
            skipped_dup += 1
            continue
        shutil.move(src, dest)
        moved += 1

    print(f"✅ 完成：移动 {moved} 个，去重删除 {skipped_dup} 个")


if __name__ == "__main__":
    main()
